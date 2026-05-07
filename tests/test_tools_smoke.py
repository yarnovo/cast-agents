"""tools 不调真 LLM · 用 httpx mock 验证 SDK 跟 schema 完整"""

import httpx

from xhs_clone_agents.tools import TOOL_SCHEMAS, XhsClient, execute_tool
from xhs_clone_agents.workspace import Workspace


def test_tool_schemas_present():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "browse_feed", "read_note", "post_note", "comment", "like", "collect",
        "send_dm", "update_memory", "schedule", "set_next_wakeup", "stop_for_now",
    } <= names
    for t in TOOL_SCHEMAS:
        assert t["input_schema"]["type"] == "object"


def test_browse_feed_via_mock(tmp_path):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={"items": [{"id": "n001", "title": "t1", "cover": "x", "ratio": 1.0, "likes": 1,
                              "author": {"id": "u01", "name": "a", "avatar": "x", "bio": "", "followers": 0, "following": 0}}],
                  "next_cursor": None},
        )
    )
    c = XhsClient(base_url="http://test", user_id="u01")
    c.http = httpx.Client(base_url="http://test", transport=transport, trust_env=False)
    items = c.browse_feed(limit=5)
    assert len(items) == 1
    c.close()


def test_local_tools(tmp_path):
    root = tmp_path / "bob"
    root.mkdir()
    ws = Workspace(name="bob", root=root, state={})
    client = XhsClient(base_url="http://test", user_id="u01")

    r1 = execute_tool("update_memory", {"line": "记一下"}, ws, client)
    assert r1 == {"ok": True}
    assert "记一下" in (root / "memory.md").read_text(encoding="utf-8")

    r2 = execute_tool("schedule", {"when": "2026-05-09 07:00", "what": "早安", "why": "聊咖啡"}, ws, client)
    assert r2["ok"] is True
    assert "早安" in (root / "calendar.md").read_text(encoding="utf-8")

    r3 = execute_tool("set_next_wakeup", {"minutes": 90}, ws, client)
    assert r3["ok"] is True
    import json
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["next_wakeup_minutes"] == 90

    r4 = execute_tool("stop_for_now", {}, ws, client)
    assert r4 == {"ok": True}
    client.close()
