import json

from cast_agents.workspace import Workspace, list_agents, load_workspace


def test_yulin_loaded():
    agents = list_agents()
    assert "yulin" in agents
    ws = load_workspace("yulin")
    assert "鹿小姐" in ws.soul
    assert "运营守则" in ws.playbook
    assert "范例 1" in ws.style
    assert ws.user_id == "u01"


def test_workspace_append_memory(tmp_path):
    root = tmp_path / "alice"
    root.mkdir()
    (root / "soul.md").write_text("alice", encoding="utf-8")
    (root / "memory.md").write_text("# alice", encoding="utf-8")
    (root / "state.json").write_text(json.dumps({"user_id": "u99"}), encoding="utf-8")
    ws = Workspace(name="alice", root=root, soul="alice", memory="# alice", state={"user_id": "u99"})
    ws.append_memory("今天发了第一条笔记")
    assert "今天发了第一条笔记" in (root / "memory.md").read_text(encoding="utf-8")


def test_workspace_calendar(tmp_path):
    root = tmp_path / "alice"
    root.mkdir()
    ws = Workspace(name="alice", root=root, calendar="", state={})
    line = ws.append_calendar("2026-05-08 07:00", "早安笔记", "聊咖啡")
    assert "2026-05-08" in line
    assert "早安笔记" in (root / "calendar.md").read_text(encoding="utf-8")


def test_workspace_state(tmp_path):
    root = tmp_path / "alice"
    root.mkdir()
    ws = Workspace(name="alice", root=root, state={})
    ws.set_state(user_id="u99", next_wakeup_minutes=60)
    saved = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert saved == {"user_id": "u99", "next_wakeup_minutes": 60}


def test_conversation(tmp_path):
    root = tmp_path / "alice"
    root.mkdir()
    ws = Workspace(name="alice", root=root, state={})
    assert ws.conversation_with("u05") == ""
    ws.append_conversation("u05", "[2026-05-07 13:00] me: 你好")
    ws.append_conversation("u05", "[2026-05-07 13:01] u05: 嗨")
    assert "你好" in ws.conversation_with("u05")
    assert "嗨" in ws.conversation_with("u05")
