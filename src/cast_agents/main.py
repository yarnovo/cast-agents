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

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# import 本模块即触发 5 个 cast 平台 tool 注册到 harness 全局 registry
import cast_platform_tools  # noqa: F401
from akong_agent_harness import Trigger, tick
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .builtin_sync import sync_all_builtin
from .config import settings


# builtin-agents/*.yaml 目录 (仓 root 下 · src/cast_agents/main.py 往上 3 级)
BUILTIN_DIR = Path(__file__).resolve().parent.parent.parent / "builtin-agents"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动钩子: 扫 builtin-agents/*.yaml · sync 到 cast-api agents 表 (架构 §D-4)"""
    if BUILTIN_DIR.exists():
        try:
            result = sync_all_builtin(settings.api_base_url, BUILTIN_DIR)
            print(
                f"[builtin-sync] synced={len(result['synced'])} "
                f"skipped={len(result['skipped'])} errors={len(result['errors'])}"
            )
            if result["errors"]:
                for slug, err in result["errors"]:
                    print(f"[builtin-sync] error {slug}: {err}")
        except Exception as e:  # noqa: BLE001
            print(f"[builtin-sync] FATAL: {type(e).__name__}: {e}")
    else:
        print(f"[builtin-sync] skip · dir not found: {BUILTIN_DIR}")
    yield


app = FastAPI(title="cast-agents", version="0.2.0", lifespan=lifespan)

# CORS · cast-app 浏览器跨域 (FC trigger 也自动加 · 但 FastAPI 自带更稳)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://m.cast.agentaily.com",
        "https://staging.m.cast.agentaily.com",
        "http://localhost:5173",  # vite dev
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


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
    import traceback
    trig = Trigger(kind=body.trigger.get("kind", "manual"), payload=body.trigger.get("payload"))
    try:
        result = tick(body.agent_id, trig, api_base_url=settings.api_base_url)
    except Exception as e:  # noqa: BLE001 · debug 暴露 stacktrace
        return {
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc().split("\n")[-12:],
            "actions": [],
            "messages": [],
            "next_wakeup": None,
            "stopped": True,
        }
    return {
        "actions": result.actions,
        "messages": result.messages,
        "next_wakeup": result.next_wakeup.isoformat() if result.next_wakeup else None,
        "stopped": result.stopped,
        "error": result.error,
    }
