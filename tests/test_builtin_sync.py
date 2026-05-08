"""builtin_sync 单测 · 不调真 cast-api · mock httpx.Client

Cases:
  - sync_one new (201 → status=created · 同步 services + tools)
  - sync_one already-exists (409 → status=exists · 跳过 services/tools)
  - sync_all_builtin 扫真 builtin-agents/*.yaml · 验 2 个 yaml 都被处理
  - sync_all_builtin 收集 errors · 不抛异常 (启动钩子 robust)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from cast_agents.builtin_sync import sync_all_builtin, sync_one


REPO_ROOT = Path(__file__).resolve().parent.parent
# builtin-agents 真源不在本仓 · 在跨平台层 ~/.claude/repos/akong/builtin-agents
# 单测 fallback 顺序:
#  1) env AKONG_BUILTIN_AGENTS_DIR (CI 配)
#  2) ~/.claude/repos/akong/builtin-agents (dev 本地 sibling)
#  3) <repo>/akong-builtin-agents (build context 临时副本)
import os as _os

_env_dir = _os.environ.get("AKONG_BUILTIN_AGENTS_DIR")
if _env_dir and Path(_env_dir).exists():
    BUILTIN_DIR = Path(_env_dir)
else:
    _dev_dir = Path.home() / ".claude" / "repos" / "akong" / "builtin-agents"
    _build_dir = REPO_ROOT / "akong-builtin-agents"
    BUILTIN_DIR = _dev_dir if _dev_dir.exists() else _build_dir


def _make_handler(routes: dict[tuple[str, str], httpx.Response]) -> httpx.MockTransport:
    """routes: {(method, path_with_query): Response}  · 不在表里的请求返 500"""

    def handler(request: httpx.Request) -> httpx.Response:
        # 包含 query string 的完整 path
        path_q = request.url.raw_path.decode()
        key = (request.method, path_q)
        if key not in routes:
            return httpx.Response(500, json={"miss": path_q})
        return routes[key]

    return httpx.MockTransport(handler)


def _client_with(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport, base_url="http://test")


def _meta_yaml() -> dict[str, Any]:
    return {
        "slug": "meta-xiaozao",
        "name": "阿空小造",
        "role": "meta",
        "owner_id": "$REAL_USER_ID",
        "soul": "meta soul",
        "playbook": "meta playbook",
        "style": "meta style",
        "tools": ["cast.send_dm", "cast.create_agent"],
        "metadata": {"tagline": "帮你打造", "avatar": "data:..."},
    }


def _design_yaml() -> dict[str, Any]:
    return {
        "slug": "design-xiaowang",
        "name": "小王",
        "role": "normal",
        "owner_id": "u_system",
        "soul": "design soul",
        "playbook": "design playbook",
        "style": "design style",
        "tools": ["cast.post"],
        "metadata": {"tagline": "5 年 LOGO", "avatar": "data:..."},
        "services": [
            {
                "title": "LOGO 草图",
                "description": "1 稿 1 改",
                "price_cents": 9900,
                "sla_hours": 24,
                "mode": "human",
            }
        ],
    }


def test_sync_one_new_creates_agent_and_syncs_services_and_tools():
    """201 → 创建成功 · services / tools 都调"""
    yaml_data = _design_yaml()
    agent_id = "ag_builtin_design-xiaowang"
    routes = {
        ("POST", f"/api/agents?owner_id=u_system&id_override={agent_id}"): httpx.Response(
            201, json={"id": agent_id}
        ),
        ("POST", f"/api/agents/{agent_id}/services?owner_id=u_system"): httpx.Response(
            201, json={"id": 1}
        ),
        ("POST", f"/api/agents/{agent_id}/tools/cast.post"): httpx.Response(201),
    }
    transport = _make_handler(routes)
    with _client_with(transport) as client:
        aid, status = sync_one(yaml_data, client)
    assert aid == agent_id
    assert status == "created"


def test_sync_one_already_exists_returns_skipped():
    """409 → status=exists · 不调 services/tools"""
    yaml_data = _design_yaml()
    agent_id = "ag_builtin_design-xiaowang"

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.raw_path.decode())
        if request.method == "POST" and "/api/agents?" in calls[-1]:
            return httpx.Response(409, json={"detail": "agent id already exists"})
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    with _client_with(transport) as client:
        aid, status = sync_one(yaml_data, client)
    assert aid == agent_id
    assert status == "exists"
    # 只调了 1 次 POST /api/agents · 不调 services / tools
    assert len(calls) == 1
    assert "/api/agents?" in calls[0]


def test_sync_one_meta_uses_u01_owner():
    """meta 角色 · owner_id 强制 u01 (单例 MVP) · 不用 yaml 的 $REAL_USER_ID"""
    yaml_data = _meta_yaml()
    agent_id = "ag_builtin_meta-xiaozao"
    routes = {
        ("POST", f"/api/agents?owner_id=u01&id_override={agent_id}"): httpx.Response(
            201, json={"id": agent_id}
        ),
        ("POST", f"/api/agents/{agent_id}/tools/cast.send_dm"): httpx.Response(201),
        ("POST", f"/api/agents/{agent_id}/tools/cast.create_agent"): httpx.Response(201),
    }
    transport = _make_handler(routes)
    with _client_with(transport) as client:
        aid, status = sync_one(yaml_data, client)
    assert aid == agent_id
    assert status == "created"


def test_sync_all_builtin_scans_real_yaml_and_collects_errors(tmp_path: Path):
    """扫真 builtin-agents/ · 拦所有 POST · 验证 meta + design 各被处理一次"""
    assert BUILTIN_DIR.exists(), f"missing {BUILTIN_DIR}"

    seen_create: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        if request.method == "POST" and path.startswith("/api/agents?"):
            # 取 id_override 做断言
            seen_create.append(path)
            return httpx.Response(201, json={"id": "ag_builtin_x"})
        if request.method == "POST" and "/services" in path:
            return httpx.Response(201, json={"id": 1})
        if request.method == "POST" and "/tools/" in path:
            return httpx.Response(201)
        return httpx.Response(500, json={"miss": path})

    # monkeypatch httpx.Client to use mock transport
    import cast_agents.builtin_sync as mod

    orig_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return orig_client(*args, **kwargs)

    mod_httpx_client = mod.httpx.Client
    mod.httpx.Client = patched_client  # type: ignore[assignment]
    try:
        result = sync_all_builtin("http://test", BUILTIN_DIR)
    finally:
        mod.httpx.Client = mod_httpx_client  # type: ignore[assignment]

    # 至少 meta-xiaozao + design-xiaowang 都 sync
    assert len(result["synced"]) >= 2
    assert "ag_builtin_meta-xiaozao" in result["synced"]
    assert "ag_builtin_design-xiaowang" in result["synced"]
    assert result["errors"] == []
    # POST /api/agents · 每 yaml 1 次
    assert len(seen_create) >= 2


def test_sync_all_builtin_robust_to_bad_yaml(tmp_path: Path):
    """坏 yaml 进 errors · 不抛异常"""
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "broken.yaml").write_text("name: no_slug_field\n", encoding="utf-8")
    (bad_dir / "empty.yaml").write_text("", encoding="utf-8")

    # 不 mock httpx · 没合法 yaml 也不会发请求
    result = sync_all_builtin("http://test", bad_dir)
    assert result["synced"] == []
    assert result["skipped"] == []
    assert len(result["errors"]) == 2
    err_slugs = {slug for slug, _ in result["errors"]}
    assert err_slugs == {"broken", "empty"}


def test_sync_all_builtin_empty_dir_returns_empty_result(tmp_path: Path):
    empty = tmp_path / "empty-dir"
    empty.mkdir()
    result = sync_all_builtin("http://test", empty)
    assert result == {"synced": [], "skipped": [], "errors": [], "filtered": []}


def test_sync_all_builtin_filters_by_consumer(tmp_path: Path):
    """yaml 显式声明 consumers · 不在列表的 consumer 跳过 (进 filtered)"""
    d = tmp_path / "mixed"
    d.mkdir()
    # cast-only yaml
    (d / "cast-only.yaml").write_text(
        "slug: cast-only\nname: cast-only\nrole: normal\nowner_id: u01\nconsumers: [cast]\n",
        encoding="utf-8",
    )
    # bilibili-only yaml (cast 跳过)
    (d / "bili-only.yaml").write_text(
        "slug: bili-only\nname: bili-only\nrole: normal\nowner_id: u01\nconsumers: [bilibili]\n",
        encoding="utf-8",
    )
    # 共享 yaml (consumers 缺省 = 全平台)
    (d / "shared.yaml").write_text(
        "slug: shared\nname: shared\nrole: normal\nowner_id: u01\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "x"})

    import cast_agents.builtin_sync as mod

    orig_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return orig_client(*args, **kwargs)

    mod.httpx.Client = patched_client  # type: ignore[assignment]
    try:
        result = sync_all_builtin("http://test", d, consumer="cast")
    finally:
        mod.httpx.Client = orig_client  # type: ignore[assignment]

    # cast-only + shared 通过 · bili-only 进 filtered
    assert "bili-only" in result["filtered"]
    assert len(result["filtered"]) == 1
    assert len(result["synced"]) == 2  # cast-only + shared 都 sync
