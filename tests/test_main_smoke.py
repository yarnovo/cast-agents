"""cast-agents 装配商烟雾测试 · 不调真 LLM / 不调真 cast-api。

验证:
  - import cast_agents.main 触发 cast-platform-tools 5 tool 注册
  - FastAPI 路由齐 (/, /health, /api/agent/tick)
  - /api/agent/tick 通过 mock harness.tick 跑通 (verify 装配链路 · 不真调 LLM)
"""

from __future__ import annotations

from datetime import datetime, timezone

from akong_agent_harness import TickResult
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
    """import cast_agents.main 必触发 cast-platform-tools 5 tool 注册到 harness 全局 registry"""
    _import_app()
    from akong_agent_harness.tools import all_registered_tools

    # 5 个 cast 平台 tool · 跟 cast-platform-tools/__init__.py 对齐
    expected = {"cast.post", "cast.send_dm", "cast.like_post", "cast.follow_user", "cast.create_agent"}
    registered = set(all_registered_tools().keys())
    assert expected <= registered, f"missing: {expected - registered}"


def test_lifespan_invokes_builtin_sync(monkeypatch):
    """启动 lifespan 必调 sync_all_builtin · 拿 settings.api_base_url + akong/builtin-agents 目录 + consumer=cast"""
    captured: dict = {}

    def fake_sync(api_base_url, builtin_dir, *, consumer=None):
        captured["api_base_url"] = api_base_url
        captured["builtin_dir"] = builtin_dir
        captured["consumer"] = consumer
        return {
            "synced": ["ag_builtin_x"],
            "skipped": [],
            "errors": [],
            "filtered": [],
        }

    import cast_agents.main as main_mod

    monkeypatch.setattr(main_mod, "sync_all_builtin", fake_sync)
    # TestClient 的 with 块进 lifespan · 退出走 shutdown
    with TestClient(main_mod.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
    assert captured["api_base_url"]
    # 新真源目录名 = builtin-agents (akong/builtin-agents) 或 akong-builtin-agents (容器内)
    assert captured["builtin_dir"].name in ("builtin-agents", "akong-builtin-agents")
    assert captured["builtin_dir"].exists()
    assert captured["consumer"] == "cast"


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
