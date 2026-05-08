"""管理面 · /agents 列表 + /agents/{name}/wakeup 触发 · /webhook xhs 平台事件钩子"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import settings
from .meta import MetaAgent
from .runtime import tick
from .scheduler import get_scheduler, schedule_cron_for_all
from .workspace import list_agents, load_workspace


@asynccontextmanager
async def lifespan(app: FastAPI):
    sch = get_scheduler()

    def cron_callback(agent_name: str) -> None:
        try:
            tick(agent_name, trigger={"kind": "cron"})
        except Exception as e:
            print(f"[cron] agent {agent_name} failed: {e}")

    schedule_cron_for_all(cron_callback, every_minutes=30)
    yield
    sch.shutdown(wait=False)


app = FastAPI(title="cast-agents", version="0.0.1", lifespan=lifespan)


class WakeupBody(BaseModel):
    trigger: dict | None = None


class WebhookBody(BaseModel):
    """xhs 平台事件钩子 · 评论 / @ / 关注 / 私信 / 点赞"""

    event: str  # "comment" | "mention" | "follow" | "dm" | "like"
    target_user_id: str  # agent 的 xhs user_id
    payload: dict


class MetaChatBody(BaseModel):
    owner_id: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]
    message: str


@app.get("/")
def root():
    return {"name": "cast-agents", "agents": list_agents(), "env": settings.env}


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.env}


@app.get("/agents")
def get_agents():
    return [
        {"name": n, "user_id": load_workspace(n).user_id, "summary": load_workspace(n).soul[:120]}
        for n in list_agents()
    ]


@app.get("/agents/{name}")
def get_agent(name: str):
    try:
        ws = load_workspace(name)
    except FileNotFoundError:
        raise HTTPException(404, "agent not found")
    return {
        "name": ws.name,
        "user_id": ws.user_id,
        "soul": ws.soul,
        "playbook": ws.playbook,
        "memory": ws.memory,
        "calendar": ws.calendar,
        "state": ws.state,
    }


@app.post("/agents/{name}/wakeup")
def wakeup(name: str, body: WakeupBody | None = None):
    try:
        return tick(name, trigger=(body.trigger if body else None) or {"kind": "manual"})
    except FileNotFoundError:
        raise HTTPException(404, "agent not found")


@app.post("/api/meta-agent/chat")
def meta_chat(body: MetaChatBody):
    """跟造物主"阿空小造"对话造分身 · 每轮独立调用"""
    meta = MetaAgent()
    try:
        return meta.chat(owner_id=body.owner_id, history=body.history, new_message=body.message)
    finally:
        meta.close()


@app.post("/webhook")
def webhook(body: WebhookBody):
    """xhs-clone-api 收到事件后调本端口 · 立刻叫醒目标 agent"""
    target = _agent_by_user_id(body.target_user_id)
    if not target:
        return {"ok": True, "skipped": "no agent owns this user_id"}
    return tick(target, trigger={"kind": body.event, "payload": body.payload})


def _agent_by_user_id(user_id: str) -> str | None:
    for n in list_agents():
        ws = load_workspace(n)
        if ws.user_id == user_id:
            return n
    return None
