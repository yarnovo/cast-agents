"""cast-agents 后端 · 单 endpoint /api/meta-agent/chat

阿空小造 (meta-agent) 跟 owner 对话引导造虚拟角色 ·
收集到完整档案后调 cast-api `/api/agents` 持久化 · 完工。
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .config import settings
from .meta import MetaAgent


app = FastAPI(title="cast-agents", version="0.1.0")


class MetaChatBody(BaseModel):
    owner_id: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]
    message: str


@app.get("/")
def root():
    return {"name": "cast-agents", "env": settings.env}


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.env}


@app.post("/api/meta-agent/chat")
def meta_chat(body: MetaChatBody):
    """跟「阿空小造」对话造虚拟角色 · 每轮独立调用 · 无状态"""
    meta = MetaAgent()
    try:
        return meta.chat(owner_id=body.owner_id, history=body.history, new_message=body.message)
    finally:
        meta.close()
