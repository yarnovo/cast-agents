"""cast-agents 装配商烟雾测试 · 不调真 LLM / 不调真 cast-api。

验证:
  - import cast_agents.main 触发 cast-platform-tools 5 tool 注册
  - FastAPI 路由齐 (/, /health, /api/agent/tick)
  - /api/agent/tick 通过 mock harness.tick 跑通 (verify 装配链路 · 不真调 LLM)
"""

from __future__ import annotations

from datetime import datetime, timezone

from akong_runtime import TickResult
from fastapi.testclient import TestClient


def _import_app():
    from cast_agents.main import app

    return app


def test_root_returns_welcome():
    client = TestClient(_import_app())
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "cast-agents"
    assert "harness" in body["description"] or "agent" in body["description"]


def test_health_ok():
    client = TestClient(_import_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_cast_tools_registered_on_import():
    """import cast_agents.main 必触发 cast-platform-tools 5 tool + meta-hermes 3 tool 注册到 akong_tools 全局 registry"""
    _import_app()
    from akong_tools import all_registered_tools

    # 5 个 cast 平台 tool (cast-platform-tools) + 3 个 meta 专属 tool (meta-hermes)
    expected_cast = {"cast.post", "cast.send_dm", "cast.like_post", "cast.follow_user", "cast.create_agent"}
    expected_meta = {"meta.create_agent", "meta.list_agents", "meta.update_agent"}
    registered = set(all_registered_tools().keys())
    assert expected_cast <= registered, f"missing cast.*: {expected_cast - registered}"
    assert expected_meta <= registered, f"missing meta.*: {expected_meta - registered}"


def test_lifespan_invokes_meta_hermes_sync(monkeypatch):
    """启动 lifespan 必调 meta_hermes.sync_meta · 拿 settings.api_base_url"""
    captured: dict = {}

    def fake_sync(api_base_url, **kwargs):
        captured["api_base_url"] = api_base_url
        return {
            "agent_id": "ag_builtin_meta-xiaozao",
            "status": "created",
            "errors": [],
        }

    import cast_agents.main as main_mod

    monkeypatch.setattr(main_mod, "sync_meta", fake_sync)
    # TestClient 的 with 块进 lifespan · 退出走 shutdown
    with TestClient(main_mod.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
    assert captured["api_base_url"]


def test_lifespan_skips_demo_when_env_unset(monkeypatch):
    """CAST_INSTALL_DEMO_AGENTS != '1' 时 lifespan 不调 demo_agents.sync_demo_agents"""
    monkeypatch.delenv("CAST_INSTALL_DEMO_AGENTS", raising=False)

    # meta sync 桩
    def fake_meta_sync(api_base_url, **kwargs):
        return {"agent_id": "ag_builtin_meta-xiaozao", "status": "exists", "errors": []}

    # 重新 import main 模块 (因为 INSTALL_DEMO 在 module 级别读 env)
    import importlib
    import cast_agents.main as main_mod
    monkeypatch.setattr(main_mod, "INSTALL_DEMO", False)
    monkeypatch.setattr(main_mod, "sync_meta", fake_meta_sync)

    demo_called = {"yes": False}

    def fake_demo_sync(*args, **kwargs):
        demo_called["yes"] = True
        return {"synced": [], "skipped": [], "errors": []}

    # 即使 demo_agents 装了 · INSTALL_DEMO=False 也不该调
    try:
        import demo_agents as demo_mod
        monkeypatch.setattr(demo_mod, "sync_demo_agents", fake_demo_sync)
    except ImportError:
        pass

    with TestClient(main_mod.app) as client:
        r = client.get("/health")
        assert r.status_code == 200

    assert demo_called["yes"] is False, "INSTALL_DEMO=False 时 lifespan 不该调 demo sync"


def test_lifespan_invokes_demo_sync_when_env_set(monkeypatch):
    """CAST_INSTALL_DEMO_AGENTS=1 + demo-agents 装好时 · lifespan 调 sync_demo_agents"""
    pytest = __import__("pytest")
    try:
        import demo_agents as demo_mod
    except ImportError:
        pytest.skip("demo-agents not installed (dev group)")

    captured: dict = {}

    def fake_meta_sync(api_base_url, **kwargs):
        return {"agent_id": "ag_builtin_meta-xiaozao", "status": "exists", "errors": []}

    def fake_demo_sync(api_base_url, **kwargs):
        captured["api_base_url"] = api_base_url
        return {
            "synced": ["ag_builtin_brand-akong"],
            "skipped": [],
            "errors": [],
        }

    import cast_agents.main as main_mod
    monkeypatch.setattr(main_mod, "INSTALL_DEMO", True)
    monkeypatch.setattr(main_mod, "sync_meta", fake_meta_sync)
    monkeypatch.setattr(demo_mod, "sync_demo_agents", fake_demo_sync)

    with TestClient(main_mod.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
    assert captured.get("api_base_url"), "INSTALL_DEMO=True 时 lifespan 必调 demo sync"


def test_tick_endpoint_calls_harness(monkeypatch):
    """POST /api/agent/tick 收到 body 后调 harness.tick · 序列化 TickResult 返回"""
    captured: dict = {}

    def fake_tick(agent_id, trigger, *, api_base_url=None, **kwargs):
        captured["agent_id"] = agent_id
        captured["trigger_kind"] = trigger.kind
        captured["trigger_payload"] = trigger.payload
        captured["api_base_url"] = api_base_url
        return TickResult(
            actions=[{"tool_id": "cast.post", "args": {"content": "hi"}, "result": {"ok": True}}],
            messages=[{"role": "assistant", "content": "done"}],
            next_wakeup=datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc),
            stopped=False,
        )

    import cast_agents.main as main_mod

    monkeypatch.setattr(main_mod, "tick", fake_tick)
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/tick",
        json={"agent_id": "ag_demo", "trigger": {"kind": "human-dm", "payload": {"from": "u_a"}}},
    )
    assert r.status_code == 200
    body = r.json()
    assert captured["agent_id"] == "ag_demo"
    assert captured["trigger_kind"] == "human-dm"
    assert captured["trigger_payload"] == {"from": "u_a"}
    assert captured["api_base_url"]  # config.settings.api_base_url 透传给 harness
    assert body["actions"][0]["tool_id"] == "cast.post"
    assert body["next_wakeup"] == "2026-05-09T00:00:00+00:00"
    assert body["stopped"] is False
    assert body["error"] is None
