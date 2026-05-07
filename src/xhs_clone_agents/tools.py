"""xhs-clone-api SDK + workspace 操作 · 全是 agent 能调的 tool"""

from __future__ import annotations

from typing import Any

import httpx

from .config import settings
from .workspace import Workspace


class XhsClient:
    """xhs-clone-api 的薄封 SDK · agent 通过它操作平台"""

    def __init__(self, base_url: str | None = None, user_id: str | None = None):
        self.base = (base_url or settings.api_base_url).rstrip("/")
        self.user_id = user_id
        self.http = httpx.Client(base_url=self.base, timeout=20.0, trust_env=False)

    def _q(self, **extra) -> dict:
        q = {"user_id": self.user_id} if self.user_id else {}
        q.update({k: v for k, v in extra.items() if v is not None})
        return q

    def browse_feed(self, limit: int = 10) -> list[dict]:
        r = self.http.get("/api/notes", params={"limit": limit})
        r.raise_for_status()
        return r.json()["items"]

    def read_note(self, note_id: str) -> dict:
        r = self.http.get(f"/api/notes/{note_id}")
        r.raise_for_status()
        return r.json()

    def comments_of(self, note_id: str) -> list[dict]:
        r = self.http.get(f"/api/notes/{note_id}/comments")
        r.raise_for_status()
        return r.json()

    def post_note(self, title: str, content: str, cover: str, images: list[str], tags: list[str], ratio: float = 1.0) -> dict:
        r = self.http.post(
            "/api/notes",
            json={"title": title, "content": content, "cover": cover, "images": images, "tags": tags, "ratio": ratio},
            params=self._q(),
        )
        r.raise_for_status()
        return r.json()

    def comment(self, note_id: str, text: str) -> dict:
        r = self.http.post(f"/api/notes/{note_id}/comments", json={"content": text}, params=self._q())
        r.raise_for_status()
        return r.json()

    def like(self, note_id: str) -> dict:
        r = self.http.post(f"/api/notes/{note_id}/like", params=self._q())
        r.raise_for_status()
        return r.json()

    def collect(self, note_id: str) -> dict:
        r = self.http.post(f"/api/notes/{note_id}/collect", params=self._q())
        r.raise_for_status()
        return r.json()

    # --- 私信 (后端 endpoints 同步加) ---
    def send_dm(self, to_user_id: str, text: str) -> dict:
        r = self.http.post("/api/messages", json={"to_user_id": to_user_id, "content": text}, params=self._q())
        r.raise_for_status()
        return r.json()

    def inbox_dm(self) -> list[dict]:
        r = self.http.get("/api/messages/inbox", params=self._q())
        r.raise_for_status()
        return r.json()

    def conversation_with(self, other_user_id: str) -> list[dict]:
        r = self.http.get(f"/api/messages/with/{other_user_id}", params=self._q())
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self.http.close()


# === LLM tool schema (Anthropic 格式) ===
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "browse_feed",
        "description": "查看小红书首页瀑布流 · 返回最新 N 条笔记摘要 · 用于决定是否互动",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
    },
    {
        "name": "read_note",
        "description": "看一条笔记详情 + 评论 · 用于深入了解后再决定评论 / 点赞",
        "input_schema": {"type": "object", "properties": {"note_id": {"type": "string"}}, "required": ["note_id"]},
    },
    {
        "name": "post_note",
        "description": "发一条新笔记 · 标题 + 正文 + 封面 (data URI 或 URL) + 图片列表 + 标签",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "笔记标题 · ≤ 50 字 · 抓眼"},
                "content": {"type": "string", "description": "正文 · 第一人称 · 跟你的人设跟文风一致"},
                "cover": {"type": "string", "description": "封面 (data URI 占位也行)"},
                "images": {"type": "array", "items": {"type": "string"}, "default": []},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "ratio": {"type": "number", "default": 1.25, "description": "封面 H/W 比例"},
            },
            "required": ["title", "content", "cover"],
        },
    },
    {
        "name": "comment",
        "description": "在一条笔记下评论",
        "input_schema": {
            "type": "object",
            "properties": {"note_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["note_id", "text"],
        },
    },
    {
        "name": "like",
        "description": "点赞一条笔记 · 已点会取消",
        "input_schema": {"type": "object", "properties": {"note_id": {"type": "string"}}, "required": ["note_id"]},
    },
    {
        "name": "collect",
        "description": "收藏一条笔记",
        "input_schema": {"type": "object", "properties": {"note_id": {"type": "string"}}, "required": ["note_id"]},
    },
    {
        "name": "send_dm",
        "description": "给某用户发私信 · 一对一对话 · 跟评论不一样 · 私下沟通",
        "input_schema": {
            "type": "object",
            "properties": {"to_user_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["to_user_id", "text"],
        },
    },
    {
        "name": "update_memory",
        "description": "把这次互动里值得长期记住的事写进自己的记忆 · 比如 '今天关注了 yulin' · '决定主打咖啡店打卡内容'",
        "input_schema": {"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"]},
    },
    {
        "name": "schedule",
        "description": "给自己设个日历提醒 · 到点 runtime 会叫醒你执行这件事",
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "ISO 时间 (2026-05-08 07:00) 或 cron (每周日 20:00)"},
                "what": {"type": "string", "description": "要做的事"},
                "why": {"type": "string", "description": "为啥要做 · 给未来的自己提醒"},
            },
            "required": ["when", "what"],
        },
    },
    {
        "name": "set_next_wakeup",
        "description": "干完这一轮 · 告诉 runtime 你下一次想几点醒 (单次闹钟 · 不入日历)",
        "input_schema": {
            "type": "object",
            "properties": {"minutes": {"type": "integer", "description": "多少分钟后再醒 · 1-1440"}},
            "required": ["minutes"],
        },
    },
    {
        "name": "stop_for_now",
        "description": "本轮没事可干 · 直接收工 · 不调用其他 tool",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def execute_tool(name: str, args: dict, ws: Workspace, client: XhsClient) -> dict:
    """执行一个 LLM 决定的 tool · 返回结果 (会喂回给 LLM)"""

    if name == "browse_feed":
        return {"items": client.browse_feed(args.get("limit", 10))}
    if name == "read_note":
        nid = args["note_id"]
        return {"note": client.read_note(nid), "comments": client.comments_of(nid)}
    if name == "post_note":
        ratio = float(args.get("ratio", 1.25))
        return client.post_note(
            title=args["title"],
            content=args["content"],
            cover=args["cover"],
            images=args.get("images", []),
            tags=args.get("tags", []),
            ratio=ratio,
        )
    if name == "comment":
        return client.comment(args["note_id"], args["text"])
    if name == "like":
        return client.like(args["note_id"])
    if name == "collect":
        return client.collect(args["note_id"])
    if name == "send_dm":
        return client.send_dm(args["to_user_id"], args["text"])

    if name == "update_memory":
        ws.append_memory(args["line"])
        return {"ok": True}
    if name == "schedule":
        line = ws.append_calendar(args["when"], args["what"], args.get("why", ""))
        return {"ok": True, "added": line}
    if name == "set_next_wakeup":
        ws.set_state(next_wakeup_minutes=int(args["minutes"]))
        return {"ok": True, "in_minutes": int(args["minutes"])}
    if name == "stop_for_now":
        return {"ok": True}

    raise ValueError(f"unknown tool: {name}")
