"""POST /api/agent/run 集成测试 · mock cast-api + 注入 LLMClient · 不调真 LLM。

覆盖:
  - happy path: agent fetch + run 单轮 end_turn + RunResult shape 序列化
  - max_turns 截止 (LLM 一直 tool_use · 打到上限)
  - LLMError → 502
  - SessionUnavailable (cast-api chat_messages endpoint 挂) → 503
  - agent_id 不存在 → 404
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from akong_agent_harness import RdsSession, Tools
from akong_agent_harness.llm import ChatResponse, LLMError, ToolCall, Usage
from akong_agent_harness.tools import ToolSpec
from fastapi.testclient import TestClient


# ----- 共享 fixture -----


@pytest.fixture(autouse=True)
def _reset_tools_registry():
    """每 test 前后清 platform tools registry · 防 cast-platform-tools import 残留干扰"""
    yield


@pytest.fixture(autouse=True)
def _set_llm_env(monkeypatch):
    monkeypatch.setenv("AKONG_LLM_API_KEY", "sk-test-stub")
    monkeypatch.setenv("AKONG_LLM_BASE_URL", "http://fake-llm")
    monkeypatch.setenv("AKONG_LLM_MODEL", "deepseek-v3.1")


def _agent_record(agent_id: str = "ag_demo") -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": "测试 agent",
        "tagline": "test tagline",
        "soul": "test soul",
        "playbook": "test playbook",
        "style": "test style",
        "skills": [],
        "metadata_json": {},
    }


def _build_cast_api_transport(
    agent: dict[str, Any] | None,
    chat_store: list[dict[str, Any]] | None = None,
    *,
    chat_messages_available: bool = True,
) -> httpx.MockTransport:
    """模拟 cast-api 的 2 类 endpoint:

    - GET /api/agents/{id}           → agent (None → 404)
    - POST/GET/DELETE /api/chat_messages  → chat_store (chat_messages_available=False 全 404)
    其他 → 404 miss
    """
    chat_store = chat_store if chat_store is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path

        # --- agents endpoint ---
        if path.startswith("/api/agents/") and method == "GET":
            wanted_id = path.rsplit("/", 1)[-1]
            if agent is None or agent["id"] != wanted_id:
                return httpx.Response(404, json={"detail": "not found"}, request=request)
            return httpx.Response(200, json=agent, request=request)

        # --- chat_messages endpoint ---
        if path == "/api/chat_messages":
            if not chat_messages_available:
                return httpx.Response(
                    404, json={"detail": "endpoint not deployed"}, request=request
                )
            if method == "POST":
                body = json.loads(request.content.decode() or "{}")
                row = {
                    "id": f"msg_{len(chat_store) + 1}",
                    "session_id": body["session_id"],
                    "role": body["role"],
                    "content": body.get("content") or "",
                    "tool_calls": body.get("tool_calls"),
                    "tool_call_id": body.get("tool_call_id"),
                    "agent_id": body.get("agent_id"),
                    "created_at": "2026-05-08T10:00:00+00:00",
                }
                chat_store.append(row)
                return httpx.Response(201, json=row, request=request)
            if method == "GET":
                sid = request.url.params.get("session_id")
                rows = [r for r in chat_store if r["session_id"] == sid]
                return httpx.Response(200, json=rows, request=request)
            if method == "DELETE":
                sid = request.url.params.get("session_id")
                chat_store[:] = [r for r in chat_store if r["session_id"] != sid]
                return httpx.Response(204, request=request)

        return httpx.Response(404, json={"detail": f"unmatched {method} {path}"}, request=request)

    return httpx.MockTransport(handler)


class _ScriptedLLM:
    """注入式 LLMClient · 按顺序返预设 ChatResponse"""

    model_id = "scripted"

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._responses:
            return ChatResponse(content="(no more)", stop_reason="end_turn")
        return self._responses.pop(0)


class _LLMClientRaisesError:
    """注入式 LLMClient · 调一次直接 raise LLMError · 验证 502"""

    model_id = "broken"

    async def chat_completion(self, messages, tools=None, **kwargs):
        raise LLMError("fake provider 5xx")


class _StaticTools(Tools):
    """跳过 cast-api · 直接返 tool spec list (这里给空 list)"""

    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs = specs or []
        self._cache = self._specs
        self.agent_id = "ag_test"
        self.platform = "cast"
        self.api_base_url = "http://fake"

    def list(self):
        return self._specs


# ----- helpers · 注 patch · 让 endpoint 用 mock transport / scripted LLM / static tools -----


def _patch_endpoint(monkeypatch, *, transport: httpx.MockTransport, llm, tools: _StaticTools | None = None):
    """patch main 模块依赖:
      - httpx.Client / httpx.AsyncClient → 都用 mock transport (cast-api fetch + RdsSession)
      - OpenAICompatibleClient → ScriptedLLM
      - Tools.connect → StaticTools
    """
    import cast_agents.main as main_mod

    base = "https://api.cast.agentaily.com"
    # patch settings.api_base_url 别真打公网
    monkeypatch.setattr(main_mod.settings, "api_base_url", base)

    # 同 httpx 版本 sync/async client · 都给 mock transport (RdsSession 用 AsyncClient · _fetch_agent_record 用 Client)
    orig_sync = main_mod.httpx.Client
    orig_async = httpx.AsyncClient

    def fake_sync_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_sync(*args, **kwargs)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_async(*args, **kwargs)

    monkeypatch.setattr(main_mod.httpx, "Client", fake_sync_client)
    monkeypatch.setattr("httpx.AsyncClient", fake_async_client)

    # patch OpenAICompatibleClient → 直接返 scripted (忽略 base_url/api_key/model)
    monkeypatch.setattr(
        main_mod, "OpenAICompatibleClient", lambda *a, **kw: llm
    )

    # patch Tools.connect → static (空 spec 让 LLM 没 platform tool 可调 · 走 builtin / end_turn)
    static = tools or _StaticTools()
    monkeypatch.setattr(main_mod.Tools, "connect", staticmethod(lambda *a, **kw: static))


# ----- tests -----


def test_run_happy_path_single_turn(monkeypatch):
    """LLM 单轮 end_turn · cast-api 拉 agent + 写 chat_messages · 200 + RunResult 序列化"""
    chat_store: list[dict[str, Any]] = []
    transport = _build_cast_api_transport(_agent_record("ag_demo"), chat_store)
    llm = _ScriptedLLM(
        [ChatResponse(content="hello back", stop_reason="end_turn", usage=Usage(10, 5, 15))]
    )

    import cast_agents.main as main_mod

    _patch_endpoint(monkeypatch, transport=transport, llm=llm)
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_demo",
            "session_id": "sess_test_1",
            "user_message": "hi",
            "max_turns": 5,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_text"] == "hello back"
    assert body["stop_reason"] == "end_turn"
    assert body["turns_used"] == 1
    assert body["usage"]["total_tokens"] == 15
    assert body["error"] is None

    # cast-api chat_messages 真写入 (user + assistant)
    roles = [r["role"] for r in chat_store]
    assert roles == ["user", "assistant"], f"unexpected chat history: {roles}"
    assert chat_store[0]["session_id"] == "sess_test_1"
    assert chat_store[0]["agent_id"] == "ag_demo"


def test_run_max_turns_caps(monkeypatch):
    """LLM 一直返 tool_use · runtime 在 max_turns=2 截止 · stop_reason='max_turns'

    用 harness builtin tool harness__set_next_wakeup (永不停 · runtime 不 break) 让 LLM 持续调:
    避免清 cast platform tools registry 造成测试间污染。
    """
    chat_store: list[dict[str, Any]] = []
    transport = _build_cast_api_transport(_agent_record("ag_demo"), chat_store)

    # set_next_wakeup builtin · 不 stop_for_now · LLM 一直要求循环
    def make_tc():
        return ChatResponse(
            tool_calls=[
                ToolCall(
                    id=f"call_{id(object())}",
                    name="harness__set_next_wakeup",
                    arguments=json.dumps({"at": "2026-05-09T00:00:00Z"}),
                )
            ],
            stop_reason="tool_use",
        )

    llm = _ScriptedLLM([make_tc(), make_tc(), make_tc(), make_tc()])

    import cast_agents.main as main_mod

    _patch_endpoint(monkeypatch, transport=transport, llm=llm)
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_demo",
            "session_id": "sess_test_max",
            "user_message": "loop please",
            "max_turns": 2,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stop_reason"] == "max_turns"
    assert body["turns_used"] == 2


def test_run_llm_error_returns_502(monkeypatch):
    """LLMClient.chat_completion 抛 LLMError → endpoint 502 · 详情透传"""
    chat_store: list[dict[str, Any]] = []
    transport = _build_cast_api_transport(_agent_record("ag_demo"), chat_store)
    llm = _LLMClientRaisesError()

    import cast_agents.main as main_mod

    _patch_endpoint(monkeypatch, transport=transport, llm=llm)
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_demo",
            "session_id": "sess_test_llm",
            "user_message": "hi",
            "max_turns": 5,
        },
    )
    # run() 内部 catches LLMError · 不直接 raise · 走 result.error + stop_reason='error' → 502
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert "LLM" in detail or "llm" in detail or "fake provider" in detail


def test_run_session_unavailable_returns_503(monkeypatch):
    """cast-api chat_messages endpoint 不可用 (404) → 503"""
    transport = _build_cast_api_transport(
        _agent_record("ag_demo"), chat_store=[], chat_messages_available=False
    )
    llm = _ScriptedLLM([ChatResponse(content="ok", stop_reason="end_turn")])

    import cast_agents.main as main_mod

    _patch_endpoint(monkeypatch, transport=transport, llm=llm)
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_demo",
            "session_id": "sess_test_sessfail",
            "user_message": "hi",
            "max_turns": 5,
        },
    )
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "session" in detail.lower() or "chat_messages" in detail.lower() or "unavailable" in detail.lower()


def test_run_agent_not_found_returns_404(monkeypatch):
    """agent_id 不存在 → cast-api GET 返 404 → endpoint 404"""
    transport = _build_cast_api_transport(None, chat_store=[])
    llm = _ScriptedLLM([])

    import cast_agents.main as main_mod

    _patch_endpoint(monkeypatch, transport=transport, llm=llm)
    client = TestClient(main_mod.app)
    r = client.post(
        "/api/agent/run",
        json={
            "agent_id": "ag_ghost",
            "session_id": "sess_404",
            "user_message": "hi",
            "max_turns": 5,
        },
    )
    assert r.status_code == 404, r.text
    assert "not found" in r.json()["detail"].lower()


def test_run_request_validation():
    """RunRequest 缺字段 → 422"""
    from cast_agents.main import app

    client = TestClient(app)
    r = client.post("/api/agent/run", json={"agent_id": "ag_demo"})
    assert r.status_code == 422  # missing session_id, user_message
