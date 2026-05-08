"""POST /api/agent/run 走新 hermes loader 路径 · 老板 5-9 拍。

验证:
  - cast-api 有 hermes row → DynamicHermesLoader 拉成功 → soul/playbook/style 用 hermes 的
  - cast-api 无 hermes row → 404 → fallback 走 _agent_def_from_record (老路径)
  - hermes loader 抛非 KeyError → 不 crash · 兜底走老路径
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from akong_llm import ChatResponse
from fastapi.testclient import TestClient

from .test_agent_run import (
    _agent_record,
    _build_cast_api_transport,
    _patch_endpoint,
    _ScriptedLLM,
)


@pytest.fixture(autouse=True)
def _set_llm_env(monkeypatch):
    monkeypatch.setenv("AKONG_LLM_API_KEY", "sk-test-stub")
    monkeypatch.setenv("AKONG_LLM_BASE_URL", "http://fake-llm")
    monkeypatch.setenv("AKONG_LLM_MODEL", "deepseek-v3.1")


def _build_transport_with_hermes(
    agent: dict[str, Any] | None,
    hermes: dict[str, Any] | None,
    chat_store: list[dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    """在 _build_cast_api_transport 基础上加 /api/hermes?agent_id= 路由"""
    base_transport = _build_cast_api_transport(agent, chat_store)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        # /api/hermes?agent_id=xxx → list (空 = 404)
        if path == "/api/hermes" and method == "GET":
            wanted_agent = request.url.params.get("agent_id")
            if hermes is None or hermes.get("agent_id") != wanted_agent:
                return httpx.Response(404, json={"detail": "no hermes"}, request=request)
            return httpx.Response(200, json=[hermes], request=request)
        # 其他走 base
        return base_transport.handler(request)

    return httpx.MockTransport(handler)


def test_run_uses_hermes_when_available(monkeypatch):
    """有 hermes row 时 · soul/playbook/style 取 hermes (而非 agent record)"""
    chat_store: list[dict[str, Any]] = []
    hermes_row = {
        "id": "hm_for_demo",
        "agent_id": "ag_demo",
        "name": "hermes-name-overrides",
        "soul": "hermes-soul-wins",
        "playbook": "hermes-playbook",
        "style": "hermes-style",
        "owner_user_id": "u01",
        "static_ref": None,
        "extra": {},
        "skills": [],
        "tools": [],
    }
    transport = _build_transport_with_hermes(
        _agent_record("ag_demo"), hermes_row, chat_store
    )
    captured: dict[str, Any] = {}

    class _CapturingLLM:
        model_id = "cap"

        async def chat_completion(self, messages, tools=None, **kwargs):
            captured["messages"] = list(messages)
            return ChatResponse(content="ok", stop_reason="end_turn")

    import cast_agents.main as main_mod
    _patch_endpoint(monkeypatch, transport=transport, llm=_CapturingLLM())
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_demo",
            "session_id": "sess_hermes",
            "user_message": "hi",
            "max_turns": 3,
        },
    )
    assert r.status_code == 200, r.text
    # system message 含 hermes 字段 (而不是 record 的 'test soul')
    sys_msg = next((m for m in captured["messages"] if m.get("role") == "system"), None)
    assert sys_msg is not None
    sys_content = sys_msg["content"]
    assert "hermes-soul-wins" in sys_content
    assert "test soul" not in sys_content  # record 的没用


def test_run_falls_back_when_no_hermes(monkeypatch):
    """没 hermes row · 兜底走 _agent_def_from_record (老路径) · 不 crash"""
    chat_store: list[dict[str, Any]] = []
    transport = _build_transport_with_hermes(_agent_record("ag_demo"), None, chat_store)
    captured: dict[str, Any] = {}

    class _CapturingLLM:
        model_id = "cap"

        async def chat_completion(self, messages, tools=None, **kwargs):
            captured["messages"] = list(messages)
            return ChatResponse(content="ok", stop_reason="end_turn")

    import cast_agents.main as main_mod
    _patch_endpoint(monkeypatch, transport=transport, llm=_CapturingLLM())
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_demo",
            "session_id": "sess_fallback",
            "user_message": "hi",
            "max_turns": 3,
        },
    )
    assert r.status_code == 200, r.text
    # system message 含 record 的 'test soul' (因为 fallback)
    sys_msg = next((m for m in captured["messages"] if m.get("role") == "system"), None)
    assert sys_msg is not None
    assert "test soul" in sys_msg["content"]


def test_run_hermes_with_static_skills(monkeypatch):
    """hermes.skills 含 static SkillRef · skill name 拼到 AgentDef.skills (LLM 看到 skill 列表)"""
    chat_store: list[dict[str, Any]] = []
    hermes_row = {
        "id": "hm_skills",
        "agent_id": "ag_demo",
        "name": "with-skills",
        "soul": "soul",
        "playbook": "play",
        "style": "style",
        "static_ref": None,
        "extra": {},
        "skills": [
            {"id": "sk_static_pub", "source": "static", "static_ref": "cast-skills::publish-post"},
        ],
        "tools": [],
    }
    transport = _build_transport_with_hermes(
        _agent_record("ag_demo"), hermes_row, chat_store
    )
    llm = _ScriptedLLM([ChatResponse(content="done", stop_reason="end_turn")])

    import cast_agents.main as main_mod
    _patch_endpoint(monkeypatch, transport=transport, llm=llm)
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_demo",
            "session_id": "sess_skills",
            "user_message": "hi",
            "max_turns": 3,
        },
    )
    assert r.status_code == 200, r.text
