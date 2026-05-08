"""cast-agents 后端装配商 · 通用 agent harness 跑 cast 平台 tools。

4 个 endpoint:
  GET  /              欢迎信息
  GET  /health        健康检查
  POST /api/agent/tick  老入口 · sync 单轮 · cast-app /create 现链路依赖 (不动)
  POST /api/agent/run   新入口 · sync 多轮 tool use loop · 老板 5-8 砍 streaming · 推荐用

不再带 hardcode meta-agent / OpenAI client / 业务 prompt:
  - LLM 调用走 akong_agent_harness.runtime.tick / .run
  - cast 平台 5 个 tool (post / dm / like_post / follow_user / create_agent) 通过 import cast_platform_tools 自动注册到全局 registry
  - meta agent (阿空小造) 不在本仓 · 由 cast-api 在 agents 表 seed 第一行 · runtime 按 agent_id 拉 6 件套自动跑

cast-app 旧入口 /api/meta-agent/chat 已砍 · cast-app /create 页 (CreateRolePage) 当前会 500 ·
等 cast-api 落地 "按 owner_id seed meta agent" + cast-app 改调 /api/agent/run 后恢复。
"""

from __future__ import annotations

import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

# import 本模块即触发 5 个 cast 平台 tool 注册到 harness 全局 registry
import cast_platform_tools  # noqa: F401
from akong_agent_harness import (
    AgentDef,
    LLMError,
    OpenAICompatibleClient,
    RdsSession,
    SessionUnavailable,
    Tools,
    Trigger,
    default_skill_registry,
    run as harness_run,
    tick,
)
from akong_agent_harness.memory import RdsAdapter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .builtin_sync import sync_all_builtin
from .config import settings


# akong/builtin-agents/*.yaml 目录 · 跨平台真源 (~/.claude/repos/akong/builtin-agents/)
# 容器内由 Dockerfile COPY 进 /app/akong-builtin-agents · 通过 env override
# dev 本地默认 fallback 到 ~/.claude/repos/akong/builtin-agents (sibling 布局)
_DEV_FALLBACK = Path.home() / ".claude" / "repos" / "akong" / "builtin-agents"
_CONTAINER_PATH = Path("/app/akong-builtin-agents")
BUILTIN_DIR = Path(
    os.environ.get("AKONG_BUILTIN_AGENTS_DIR")
    or (str(_CONTAINER_PATH) if _CONTAINER_PATH.exists() else str(_DEV_FALLBACK))
)

# 本仓 = cast 平台消费方 · sync_all_builtin 用 consumer="cast" 过滤跨平台 yaml
CONSUMER = "cast"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动钩子: 扫 akong/builtin-agents/*.yaml · sync 到 cast-api agents 表 (架构 §D-4)"""
    if BUILTIN_DIR.exists():
        try:
            result = sync_all_builtin(settings.api_base_url, BUILTIN_DIR, consumer=CONSUMER)
            print(
                f"[builtin-sync] dir={BUILTIN_DIR} consumer={CONSUMER} "
                f"synced={len(result['synced'])} skipped={len(result['skipped'])} "
                f"filtered={len(result.get('filtered', []))} errors={len(result['errors'])}"
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


# ====================== /api/agent/run · 新入口 (老板 5-8 砍 streaming · sync 一次性返) ======================


class RunRequest(BaseModel):
    agent_id: str
    session_id: str  # caller 自管 (cast-app 一次会话 1 个 · 跨 tick 复用)
    user_message: str
    max_turns: int = 10


def _fetch_agent_record(agent_id: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """从 cast-api 拉 agent row · 不存在 → 404 (HTTPException)"""
    base = settings.api_base_url.rstrip("/")
    try:
        with httpx.Client(base_url=base, timeout=timeout, trust_env=False) as client:
            resp = client.get(f"/api/agents/{agent_id}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"cast-api unreachable: {e}") from e
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"cast-api GET /api/agents/{agent_id} → {resp.status_code} {resp.text[:200]}",
        )
    return resp.json()


def _agent_def_from_record(rec: dict[str, Any]) -> AgentDef:
    """cast-api agent row → harness AgentDef · 兼容 metadata_json (可能 str / dict)"""
    meta = rec.get("metadata_json")
    if isinstance(meta, str):
        try:
            import json as _json
            meta = _json.loads(meta)
        except (ValueError, TypeError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    skills_raw = rec.get("skills")
    if skills_raw is None:
        skills_raw = meta.get("skills")
    if isinstance(skills_raw, str):
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
    elif isinstance(skills_raw, list):
        skills = [str(s) for s in skills_raw]
    else:
        skills = []
    return AgentDef(
        id=rec["id"],
        name=rec.get("name") or "",
        tagline=rec.get("tagline") or "",
        soul=rec.get("soul") or "",
        playbook=rec.get("playbook") or "",
        style=rec.get("style") or "",
        skills=skills,
        metadata=meta,
    )


@app.post("/api/agent/run")
async def agent_run(req: RunRequest) -> dict[str, Any]:
    """harness `run()` 入口 · sync 多轮 tool use loop · 一次性返 RunResult。

    步骤:
      1. 从 cast-api 拉 agent row → AgentDef
      2. 装 RdsSession(session_id, agent_id) → cast-api /api/chat_messages 持久化 (跨 FC sticky)
      3. 装 OpenAICompatibleClient (env: AKONG_LLM_*)
      4. 装 Tools/Memory (cast-api · best-effort) + skills (default_skill_registry)
      5. await harness.run(...) · 返 RunResult.dict

    错误归因:
      - agent_id 不存在 → 404
      - cast-api 不可达 / 5xx → 503
      - chat_messages endpoint 挂 (SessionUnavailable) → 503
      - LLM provider 失败 (LLMError) → 502
      - 其他 unexpected → 500 + traceback (debug)
    """
    # 1. agent row → AgentDef
    record = _fetch_agent_record(req.agent_id)
    agent = _agent_def_from_record(record)

    # 2. session (cast-api chat_messages)
    session = RdsSession(
        req.session_id,
        api_base_url=settings.api_base_url,
        agent_id=req.agent_id,
    )

    # 3. LLM client (env-driven)
    llm_api_key = os.environ.get("AKONG_LLM_API_KEY", "")
    llm_base_url = os.environ.get(
        "AKONG_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    llm_model = os.environ.get("AKONG_LLM_MODEL", "deepseek-v3.1")
    if not llm_api_key:
        raise HTTPException(status_code=500, detail="AKONG_LLM_API_KEY env not set")
    llm = OpenAICompatibleClient(
        base_url=llm_base_url, api_key=llm_api_key, model=llm_model
    )

    # 4. tools + memory (best-effort · 单 agent 装配)
    tools = Tools.connect(req.agent_id, platform="cast", api_base_url=settings.api_base_url)
    memory = RdsAdapter(req.agent_id, api_base_url=settings.api_base_url)
    skill_registry = default_skill_registry()

    # 5. run · 错误归因
    try:
        result = await harness_run(
            agent=agent,
            user_message=req.user_message,
            session=session,
            llm_client=llm,
            tools=tools,
            memory=memory,
            skills=skill_registry,
            max_turns=req.max_turns,
        )
    except SessionUnavailable as e:
        raise HTTPException(
            status_code=503,
            detail=f"chat_messages endpoint unavailable: {e}",
        ) from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}") from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc().split("\n")[-12:],
            },
        ) from e
    finally:
        # RdsSession 自带 httpx.AsyncClient · 关一下
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            pass

    # run() 内部错误也走 result.error / stop_reason='error' (不抛) · 归因到 502/503
    if result.stop_reason == "error" and result.error:
        err = result.error.lower()
        if "session" in err:
            raise HTTPException(status_code=503, detail=result.error)
        if "llm" in err:
            raise HTTPException(status_code=502, detail=result.error)
        # 其他兜底 500
        raise HTTPException(status_code=500, detail=result.error)

    return {
        "messages": result.messages,
        "final_text": result.final_text,
        "stop_reason": result.stop_reason,
        "turns_used": result.turns_used,
        "actions": result.actions,
        "usage": result.usage,
        "error": result.error,
    }
