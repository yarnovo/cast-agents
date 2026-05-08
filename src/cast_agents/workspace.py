"""读写一个 agent 的档案袋 · 一 agent 一组文件"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path

from .config import settings


@dataclass
class Workspace:
    name: str
    root: Path
    soul: str = ""
    playbook: str = ""
    style: str = ""
    memory: str = ""
    calendar: str = ""
    state: dict = field(default_factory=dict)

    @property
    def user_id(self) -> str | None:
        return self.state.get("user_id")

    @property
    def conversations_dir(self) -> Path:
        return self.root / "conversations"

    def conversation_with(self, other_user_id: str) -> str:
        p = self.conversations_dir / f"{other_user_id}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def append_conversation(self, other_user_id: str, line: str) -> None:
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        p = self.conversations_dir / f"{other_user_id}.md"
        with p.open("a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")

    def append_memory(self, line: str) -> None:
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        self.memory = (self.memory or "") + f"\n- {ts} · {line.strip()}"
        (self.root / "memory.md").write_text(self.memory, encoding="utf-8")

    def append_calendar(self, when: str, what: str, why: str = "") -> str:
        line = f"- {when} · {what}" + (f" · {why}" if why else "")
        self.calendar = (self.calendar or "") + "\n" + line
        (self.root / "calendar.md").write_text(self.calendar, encoding="utf-8")
        return line

    def set_state(self, **kwargs) -> None:
        self.state.update(kwargs)
        (self.root / "state.json").write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _read_md(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_workspace(name: str) -> Workspace:
    root = settings.agents_dir / name
    if not root.exists():
        raise FileNotFoundError(f"agent {name!r} not found at {root}")
    state_path = root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    return Workspace(
        name=name,
        root=root,
        soul=_read_md(root / "soul.md"),
        playbook=_read_md(root / "playbook.md"),
        style=_read_md(root / "style.md"),
        memory=_read_md(root / "memory.md"),
        calendar=_read_md(root / "calendar.md"),
        state=state,
    )


def list_agents() -> list[str]:
    if not settings.agents_dir.exists():
        return []
    return sorted(p.name for p in settings.agents_dir.iterdir() if p.is_dir() and (p / "soul.md").exists())
