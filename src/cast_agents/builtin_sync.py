"""启动时扫 akong/builtin-agents/*.yaml · upsert 到 cast-api agents 表

builtin-agents 真源 = ~/.claude/repos/akong/builtin-agents/ (跨平台共享)
本仓 (cast/agents) 是消费方 · 通过 env AKONG_BUILTIN_AGENTS_DIR 拿到目录路径。

MVP 简化:
  - meta 跟普通 builtin 都同步 · meta 用 owner_id=u01 单例 (不 per-user · 等真人多用户后再切)
  - 用 cast-api HTTP endpoint · 不直连 DB
  - create-if-not-exists · 不做 diff update (减少复杂度)
  - agent_id 确定: ag_builtin_<slug> (cast-api 支持 ?id_override=)

跟 cast-api 端契约:
  - POST /api/agents?owner_id=<oid>&id_override=ag_builtin_<slug>
    body 含 name/tagline/soul/playbook/style/expertise
    201 = 新建成功 · 409 = 已存在 (跳过)
  - POST /api/agents/{id}/services?owner_id=<oid>  · 每个 yaml services 项一调
  - POST /api/agents/{id}/tools/{tool_id}          · 每个 yaml tools 项一调

跨平台过滤 (consumers 字段):
  - yaml 显式声明 `consumers: [cast]` 或 `consumers: [cast, bilibili]` 时 · 只在该平台 sync
  - 没声明 = 全平台共享 (默认全部 sync)
  - 调用方传 consumer="cast" / "bilibili" 过滤
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml

# meta agent owner: MVP 单 owner · 真人多账号后切 per-user spawn
DEFAULT_META_OWNER_ID = "u01"
# 普通 builtin agent owner · yaml 里写啥用啥 · 缺省时 fallback u01
FALLBACK_OWNER_ID = "u01"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_owner_id(yaml_data: dict[str, Any]) -> str:
    """从 yaml owner_id 字段拿 owner · meta 模板用 DEFAULT_META_OWNER_ID 单例"""
    if yaml_data.get("role") == "meta":
        return DEFAULT_META_OWNER_ID
    raw = yaml_data.get("owner_id") or FALLBACK_OWNER_ID
    # template 占位符 (例: $REAL_USER_ID) · MVP 用 fallback
    if isinstance(raw, str) and raw.startswith("$"):
        return FALLBACK_OWNER_ID
    return raw


def _agent_body(yaml_data: dict[str, Any]) -> dict[str, Any]:
    """yaml → AgentCreate body

    skills 字段走 metadata_json 携带 (cast-api schema 不动 · MVP 简化)
    runtime 端 _resolve_agent_skill_slugs 会从 metadata_json.skills 读出来。
    """
    import json as _json

    metadata = dict(yaml_data.get("metadata") or {})
    rules = yaml_data.get("rules")
    # skills 数组并入 metadata_json (cast-api 没原生 skills 字段 · 走 metadata 携带)
    if "skills" in yaml_data:
        metadata["skills"] = list(yaml_data.get("skills") or [])
    return {
        "name": yaml_data["name"],
        "tagline": metadata.get("tagline", ""),
        "soul": yaml_data.get("soul", ""),
        "playbook": yaml_data.get("playbook", ""),
        "style": yaml_data.get("style", ""),
        "expertise": metadata.get("tagline", ""),  # MVP 用 tagline 兜底
        "avatar": metadata.get("avatar", ""),
        "role": yaml_data.get("role", "normal"),
        "rules_json": _json.dumps(rules, ensure_ascii=False) if rules else None,
        "metadata_json": _json.dumps(metadata, ensure_ascii=False) if metadata else None,
    }


def sync_one(yaml_data: dict[str, Any], client: httpx.Client) -> tuple[str, str]:
    """upsert 一个 builtin agent · 返 (agent_id, status)

    status:
      - "created" · 新建
      - "exists"  · 已存在 (跳过)

    步骤:
      1. agent_id = ag_builtin_<slug>
      2. POST /api/agents?owner_id=<oid>&id_override=<id> body=AgentCreate
         201 → 创建成功 · 同步 services + tools
         409 → 已存在 · 跳过 (MVP 不 diff update)
      3. raise HTTPError 其它情况
    """
    slug = yaml_data["slug"]
    agent_id = f"ag_builtin_{slug}"
    owner_id = _resolve_owner_id(yaml_data)

    body = _agent_body(yaml_data)
    r = client.post(
        f"/api/agents?owner_id={owner_id}&id_override={agent_id}",
        json=body,
    )
    if r.status_code == 409:
        return agent_id, "exists"
    if r.status_code != 201:
        r.raise_for_status()

    # 同步 services
    for svc in yaml_data.get("services") or []:
        svc_body = {
            "title": svc["title"],
            "description": svc.get("description", ""),
            "price_cents": svc["price_cents"],
            "sla_hours": svc.get("sla_hours", 72),
            "mode": svc.get("mode", "hybrid"),
        }
        sr = client.post(
            f"/api/agents/{agent_id}/services?owner_id={owner_id}",
            json=svc_body,
        )
        sr.raise_for_status()

    # 同步 tools (grant agent_tools 关联)
    for tool_id in yaml_data.get("tools") or []:
        tr = client.post(f"/api/agents/{agent_id}/tools/{tool_id}")
        # 工具 grant endpoint 可能尚未在 router 注册 · MVP 容忍 404 (架构 §2.4 基建后接)
        if tr.status_code not in (201, 204, 404, 409):
            tr.raise_for_status()

    return agent_id, "created"


def _yaml_targets_consumer(yaml_data: dict[str, Any], consumer: str | None) -> bool:
    """判断 yaml 是否 target 当前 consumer 平台。

    consumers 字段语义:
      - 缺省 / None  → 全平台共享 (任何 consumer 都 sync)
      - 列表 [a, b]  → 仅 consumer 在列表内时 sync
      - 单值字符串 (老写法) → 视作单元素列表 (兼容 metadata.consumer)

    consumer=None 时跳过过滤 (sync 全部 yaml)。
    """
    if consumer is None:
        return True
    consumers = yaml_data.get("consumers")
    if consumers is None:
        # fallback: metadata.consumer 老字段 (向后兼容 · 单值字符串)
        meta_consumer = (yaml_data.get("metadata") or {}).get("consumer")
        if meta_consumer is None:
            return True  # 没声明 = 全平台共享
        consumers = [meta_consumer] if isinstance(meta_consumer, str) else list(meta_consumer)
    if isinstance(consumers, str):
        consumers = [consumers]
    return consumer in consumers


def sync_all_builtin(
    api_base_url: str,
    builtin_dir: Path,
    consumer: str | None = None,
) -> dict[str, Any]:
    """扫 builtin-agents/*.yaml · 调 cast-api upsert

    Args:
      api_base_url  cast-api endpoint
      builtin_dir   ~/.claude/repos/akong/builtin-agents/ (或容器内 mount 路径)
      consumer      平台标识 (例 "cast" / "bilibili") · 过滤 yaml `consumers:` 字段
                    None = 不过滤 (sync 全部 · 用于 dev / 测试)

    返 {synced: list[str], skipped: list[str], errors: list[(slug, error)], filtered: list[str]}

    synced   · 本次新建的 agent_id
    skipped  · 已存在的 agent_id
    errors   · (slug, error message) tuple list · 不抛异常 (启动钩子不能阻止 lifespan)
    filtered · consumers 字段不含 consumer · 跳过的 slug
    """
    result: dict[str, Any] = {"synced": [], "skipped": [], "errors": [], "filtered": []}

    yaml_files = sorted(builtin_dir.glob("*.yaml"))
    if not yaml_files:
        return result

    # trust_env=False · 不沾系统代理 (FC 容器 / dev 机 SOCKS_PROXY 都不影响内网调 cast-api)
    with httpx.Client(base_url=api_base_url, timeout=10.0, trust_env=False) as client:
        for yaml_path in yaml_files:
            slug = yaml_path.stem
            try:
                yaml_data = _load_yaml(yaml_path)
                if not yaml_data or not yaml_data.get("slug"):
                    result["errors"].append((slug, "missing slug field"))
                    continue
                if not _yaml_targets_consumer(yaml_data, consumer):
                    result["filtered"].append(slug)
                    continue
                agent_id, status = sync_one(yaml_data, client)
                if status == "created":
                    result["synced"].append(agent_id)
                else:
                    result["skipped"].append(agent_id)
            except Exception as e:  # noqa: BLE001 · 启动钩子不能 crash
                result["errors"].append((slug, f"{type(e).__name__}: {e}"))

    return result
