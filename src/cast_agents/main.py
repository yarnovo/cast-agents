"""cast-agents 后端装配商 · 通用 agent harness 跑 cast 平台 tools。

3 个 endpoint:
  GET  /              欢迎信息
  GET  /health        健康检查
  POST /api/agent/tick  agent runtime 入口 · 调 akong_agent_harness.tick

不再带 hardcode meta-agent / OpenAI client / 业务 prompt:
  - LLM 调用走 akong_agent_harness.runtime.tick
  - cast 平台 5 个 tool (post / dm / like_post / follow_user / create_agent) 通过 import cast_platform_tools 自动注册到全局 registry
  - meta agent (阿空小造) 不在本仓 · 由 cast-api 在 agents 表 seed 第一行 · runtime 按 agent_id 拉 6 件套自动跑

cast-app 旧入口 /api/meta-agent/chat 已砍 · cast-app /create 页 (CreateRolePage) 当前会 500 ·
等 cast-api 落地 "按 owner_id seed meta agent" + cast-app 改调 /api/agent/tick 后恢复 (T5 干)。
"""

from __future__ import annotations

from typing import Any

# import 本模块即触发 5 个 cast 平台 tool 注册到 harness 全局 registry
import cast_platform_tools  # noqa: F401
from akong_agent_harness import Trigger, tick
from fastapi import FastAPI
from pydantic import BaseModel

from .config import settings


app = FastAPI(title="cast-agents", version="0.2.0")


class TickBody(BaseModel):
    agent_id: str
    trigger: dict


@app.get("/")
def root() -> dict:
    return {
        "name": "cast-agents",
        "version": "0.2.0",
        "env": settings.env,
        "description": "Cast 平台 agent 后端装配商 · 通用 agent harness 跑 cast 平台 tools",
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.env}


@app.post("/api/agent/tick")
def agent_tick(body: TickBody) -> dict[str, Any]:
    """agent runtime 入口 · 把 agent_id + trigger 转给 harness.tick。

    body:
      agent_id  ag_xxx (cast-api agents 表里的真行)
      trigger   {kind: 'cron'|'event'|'human-dm'|'manual', payload?: {...}}

    返 TickResult dict (actions / messages / next_wakeup / stopped / error)。
    """
    trig = Trigger(kind=body.trigger.get("kind", "manual"), payload=body.trigger.get("payload"))
    result = tick(body.agent_id, trig, api_base_url=settings.api_base_url)
    return {
        "actions": result.actions,
        "messages": result.messages,
        "next_wakeup": result.next_wakeup.isoformat() if result.next_wakeup else None,
        "stopped": result.stopped,
        "error": result.error,
    }
