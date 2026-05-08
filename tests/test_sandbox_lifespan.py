"""cast-agents lifespan 装 sandbox backend (老板 5-9 拍).

验证:
  - AKONG_SANDBOX_BACKEND 不设 → app.state.sandbox_client = None (不阻塞)
  - 设 agentrun 但缺 ACCOUNT_ID/API_KEY → None (不 crash · log 警告)
  - 设 local-docker → LocalDockerBackend 装上
  - 设 agentrun + ACCOUNT_ID + API_KEY → AgentRunBackend 装上
  - shutdown 调 sandbox_client.aclose() (best-effort · 异常不传)

不真打 AgentRun (lead 没开通服务前 mock 测).
"""

from __future__ import annotations

import pytest


def _reload_main_module():
    """每 test reload main module · 因为 lifespan / app 是 module-level 全局."""
    import sys

    for mod in list(sys.modules):
        if mod.startswith("cast_agents."):
            del sys.modules[mod]
    import cast_agents.main  # noqa: F401 · 触发 import-side effect

    return sys.modules["cast_agents.main"]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """不让 host env 污染测 · 也不让 demo-agents lifespan 跑真 sync."""
    for k in (
        "AKONG_SANDBOX_BACKEND",
        "AKONG_AGENTRUN_ACCOUNT_ID",
        "AKONG_AGENTRUN_API_KEY",
        "AKONG_AGENTRUN_REGION",
        "CAST_INSTALL_DEMO_AGENTS",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AKONG_LLM_API_KEY", "sk-stub")  # 防 lifespan 抛
    yield


def test_no_sandbox_env_returns_none():
    main = _reload_main_module()
    sb = main._build_sandbox_client()
    assert sb is None


def test_agentrun_missing_creds_returns_none(monkeypatch):
    main = _reload_main_module()
    monkeypatch.setenv("AKONG_SANDBOX_BACKEND", "agentrun")
    # 缺 ACCOUNT_ID + API_KEY
    sb = main._build_sandbox_client()
    assert sb is None


def test_agentrun_with_creds_builds_backend(monkeypatch):
    pytest.importorskip("akong_sandbox")
    main = _reload_main_module()
    monkeypatch.setenv("AKONG_SANDBOX_BACKEND", "agentrun")
    monkeypatch.setenv("AKONG_AGENTRUN_ACCOUNT_ID", "999888777")
    monkeypatch.setenv("AKONG_AGENTRUN_API_KEY", "secret-key-xyz")
    monkeypatch.setenv("AKONG_AGENTRUN_REGION", "cn-shanghai")

    sb = main._build_sandbox_client()
    assert sb is not None
    assert sb.account_id == "999888777"
    assert sb.api_key == "secret-key-xyz"
    assert sb.region == "cn-shanghai"
    assert sb.base_url == "https://999888777.agentrun-data.cn-shanghai.aliyuncs.com"


def test_local_docker_builds_backend(monkeypatch):
    """需要 akong-sandbox 已装 · 不在依赖里则 skip."""
    pytest.importorskip("akong_sandbox")
    main = _reload_main_module()
    monkeypatch.setenv("AKONG_SANDBOX_BACKEND", "local-docker")
    sb = main._build_sandbox_client()
    assert sb is not None
    # LocalDockerBackend 满足 SandboxClient Protocol shape (run_python / run_shell / aclose)
    assert hasattr(sb, "run_python")
    assert hasattr(sb, "run_shell")
    assert hasattr(sb, "aclose")


def test_unknown_backend_returns_none(monkeypatch):
    main = _reload_main_module()
    monkeypatch.setenv("AKONG_SANDBOX_BACKEND", "frobnicator")
    sb = main._build_sandbox_client()
    assert sb is None


@pytest.mark.asyncio
async def test_agentrun_aclose_called_in_lifespan(monkeypatch):
    """lifespan finally 调 sandbox.aclose() · 不传异常."""
    import httpx

    monkeypatch.setenv("AKONG_SANDBOX_BACKEND", "agentrun")
    monkeypatch.setenv("AKONG_AGENTRUN_ACCOUNT_ID", "acc")
    monkeypatch.setenv("AKONG_AGENTRUN_API_KEY", "key")
    main = _reload_main_module()

    aclose_calls: list[bool] = []

    class _SpyBackend:
        account_id = "acc"
        api_key = "key"
        region = "cn-hangzhou"
        base_url = "https://acc.agentrun-data.cn-hangzhou.aliyuncs.com"

        async def run_python(self, code, timeout=30, env=None):
            return None

        async def run_shell(self, cmd, timeout=30, env=None):
            return None

        async def aclose(self):
            aclose_calls.append(True)

    monkeypatch.setattr(main, "_build_sandbox_client", lambda: _SpyBackend())

    # 跳过 demo / meta-hermes lifespan 真调用 · 让 lifespan 走完即可
    monkeypatch.setattr(
        main,
        "sync_meta",
        lambda *_a, **_kw: {"agent_id": "ag_meta", "status": "ok", "errors": []},
    )
    monkeypatch.setattr(
        main,
        "sync_to_cast_api_hermes_table",
        lambda *_a, **_kw: {"hermes_id": "hm_meta", "status": "ok", "errors": []},
    )

    async with main.lifespan(main.app):
        # yield 期 sandbox_client 已装
        assert hasattr(main.app.state, "sandbox_client")
        assert isinstance(main.app.state.sandbox_client, _SpyBackend)

    # exit 后 aclose 已调
    assert aclose_calls == [True]


@pytest.mark.asyncio
async def test_aclose_failure_swallowed(monkeypatch):
    """sandbox.aclose() 抛 · lifespan 不 crash · 只 log."""
    monkeypatch.setenv("AKONG_LLM_API_KEY", "sk-stub")
    main = _reload_main_module()

    class _BrokenBackend:
        async def aclose(self):
            raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(main, "_build_sandbox_client", lambda: _BrokenBackend())
    monkeypatch.setattr(
        main, "sync_meta", lambda *_a, **_kw: {"agent_id": "x", "status": "ok", "errors": []}
    )
    monkeypatch.setattr(
        main,
        "sync_to_cast_api_hermes_table",
        lambda *_a, **_kw: {"hermes_id": "x", "status": "ok", "errors": []},
    )

    # 不应抛
    async with main.lifespan(main.app):
        pass
