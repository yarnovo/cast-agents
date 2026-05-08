"""cast-agents 后端装配商 · 通用 agent harness 跑 cast 平台 tools。

4 个 endpoint:
  GET  /              欢迎信息
  GET  /health        健康检查
  POST /api/agent/tick  老入口 · sync 单轮 · cast-app /create 现链路依赖 (不动)
  POST /api/agent/run   新入口 · sync 多轮 tool use loop · 老板 5-8 砍 streaming · 推荐用

不再带 hardcode meta-agent / OpenAI client / 业务 prompt:
  - LLM 调用走 akong_runtime.tick / .run
  - cast 平台 5 个 tool (post / dm / like_post / follow_user / create_agent) 通过 import cast_platform_tools 自动注册到全局 registry (akong_tools)
  - meta agent (阿空小造) 不在本仓 · 由 cast-api 在 agents 表 seed 第一行 · runtime 按 agent_id 拉 6 件套自动跑

cast-app 旧入口 /api/meta-agent/chat 已砍 · cast-app /create 页 (CreateRolePage) 当前会 500 ·
等 cast-api 落地 "按 owner_id seed meta agent" + cast-app 改调 /api/agent/run 后恢复。
"""

from __future__ import annotations

import os
import traceback
from contextlib import asynccontextmanager
from typing import Any

import httpx

# import 本模块即触发 5 个 cast 平台 tool 注册到 akong_tools 全局 registry
import cast_platform_tools  # noqa: F401

# import meta-hermes 即触发 3 个 meta.* tool 注册 (meta.create_agent / list_agents / update_agent)
import meta_hermes  # noqa: F401
from akong_hermes import (
    DynamicHermesLoader,
    Hermes,
    SkillResolver,
    ToolResolver,
)

# SandboxClient Protocol · akong-hermes feat/sandbox-client-injection 引入 (老板 5-9)
# 通过 try import 兼容 main 分支 · 等 hermes PR merge 后转硬 import.
try:
    from akong_hermes import SandboxClient
except ImportError:  # akong-hermes main 还没 merge 时
    SandboxClient = None  # type: ignore[assignment,misc]
from akong_llm import LLMError, OpenAICompatibleClient
from akong_memory import RdsAdapter
from akong_runtime import (
    AgentDef,
    Trigger,
    run as harness_run,
    tick,
)
from akong_session import RdsSession, SessionUnavailable
from akong_skills import default_registry as default_skill_registry
from akong_tools import Tools
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from meta_hermes import sync_meta, sync_to_cast_api_hermes_table
from pydantic import BaseModel

from .config import settings


# demo-agents 是可选依赖 · 装上才能 import (生产默认不装)
# env CAST_INSTALL_DEMO_AGENTS=1 时 lifespan 调 sync · 否则 import 也不调 sync
INSTALL_DEMO = os.environ.get("CAST_INSTALL_DEMO_AGENTS") == "1"


def _build_sandbox_client():
    # type: ignore[no-untyped-def]
    # 返 SandboxClient | None · annotation 弱化避免 main 没 SandboxClient 时崩
    """按 env 装 sandbox backend (老板 5-9 拍 · prod AgentRun · dev LocalDocker · 不配则 None).

    Returns:
      SandboxClient 实例 (AgentRunBackend / LocalDockerBackend) · 或 None 表示不启用
      sandbox 真跑 (dynamic_python skill / tool 直接走 stub).

    env:
      AKONG_SANDBOX_BACKEND   'agentrun' (prod) / 'local-docker' (dev) / 不设 = 不启用
      AKONG_AGENTRUN_ACCOUNT_ID  agentrun 必配
      AKONG_AGENTRUN_API_KEY     agentrun 必配
      AKONG_AGENTRUN_REGION      默认 cn-hangzhou
    """
    backend_name = os.environ.get("AKONG_SANDBOX_BACKEND", "").lower()
    if not backend_name:
        return None
    if backend_name == "agentrun":
        try:
            from akong_sandbox import AgentRunBackend
        except ImportError:
            print("[sandbox] AKONG_SANDBOX_BACKEND=agentrun 但 akong-sandbox 未装 · 跳过")
            return None
        account_id = os.environ.get("AKONG_AGENTRUN_ACCOUNT_ID", "")
        api_key = os.environ.get("AKONG_AGENTRUN_API_KEY", "")
        if not account_id or not api_key:
            print(
                "[sandbox] AKONG_SANDBOX_BACKEND=agentrun 但缺 ACCOUNT_ID / API_KEY · 跳过"
            )
            return None
        region = os.environ.get("AKONG_AGENTRUN_REGION", "cn-hangzhou")
        print(f"[sandbox] AgentRunBackend ready · account={account_id} region={region}")
        return AgentRunBackend(account_id=account_id, api_key=api_key, region=region)
    if backend_name == "local-docker":
        try:
            from akong_sandbox import LocalDockerBackend
        except ImportError:
            print("[sandbox] AKONG_SANDBOX_BACKEND=local-docker 但 akong-sandbox 未装 · 跳过")
            return None
        print("[sandbox] LocalDockerBackend ready")
        return LocalDockerBackend()
    print(f"[sandbox] 不识别 AKONG_SANDBOX_BACKEND={backend_name} · 跳过")
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动钩子: 灌 meta agent (必装) + 可选灌 demo agents (env opt-in) + 装 sandbox backend (老板 5-9 拍).

    老板 5-9 拍拆仓:
      - 静态 hermes (meta-hermes) · 必装 · 平台核心入口
      - demo / 种子 agent (demo-agents) · 默认不装 · CAST_INSTALL_DEMO_AGENTS=1 才装
      - sandbox client (akong-sandbox AgentRunBackend) · 装上后注入 SkillResolver / ToolResolver · 跑 dynamic_python
    """
    # 0. sandbox backend (老板 5-9 拍 · 没配 env 则 None · 不阻塞启动)
    sandbox_client = _build_sandbox_client()
    app.state.sandbox_client = sandbox_client
    # 1. meta-hermes · 必装
    try:
        meta_result = sync_meta(settings.api_base_url)
        print(
            f"[meta-hermes] sync agent_id={meta_result['agent_id']} "
            f"status={meta_result['status']} errors={len(meta_result.get('errors') or [])}"
        )
        if meta_result.get("errors"):
            for err in meta_result["errors"]:
                print(f"[meta-hermes] error: {err}")
    except Exception as e:  # noqa: BLE001 · 启动钩子不能 crash
        print(f"[meta-hermes] FATAL: {type(e).__name__}: {e}")

    # 1b. 同步 meta hermes 行 (老板 5-9 拍 · best-effort · cast-api 没 hermes 表 → 跳过)
    try:
        hermes_result = sync_to_cast_api_hermes_table(settings.api_base_url)
        print(
            f"[meta-hermes] sync hermes hermes_id={hermes_result['hermes_id']} "
            f"status={hermes_result['status']} errors={len(hermes_result.get('errors') or [])}"
        )
        if hermes_result.get("errors"):
            for err in hermes_result["errors"]:
                print(f"[meta-hermes] hermes error: {err}")
    except Exception as e:  # noqa: BLE001
        print(f"[meta-hermes] hermes sync skipped: {type(e).__name__}: {e}")

    # 2. demo-agents · 可选
    if INSTALL_DEMO:
        try:
            from demo_agents import sync_demo_agents

            demo_result = sync_demo_agents(settings.api_base_url)
            print(
                f"[demo-agents] synced={len(demo_result['synced'])} "
                f"skipped={len(demo_result['skipped'])} errors={len(demo_result['errors'])}"
            )
            if demo_result["errors"]:
                for slug, err in demo_result["errors"]:
                    print(f"[demo-agents] error {slug}: {err}")
        except ImportError:
            print("[demo-agents] CAST_INSTALL_DEMO_AGENTS=1 但包未装 · 装 demo extra: uv sync --extra demo")
        except Exception as e:  # noqa: BLE001
            print(f"[demo-agents] FATAL: {type(e).__name__}: {e}")
    else:
        print("[demo-agents] skip · CAST_INSTALL_DEMO_AGENTS != '1'")

    try:
        yield
    finally:
        # shutdown · 释放 sandbox 实例 (省费用)
        if sandbox_client is not None:
            try:
                await sandbox_client.aclose()
                print("[sandbox] aclose ok")
            except Exception as e:  # noqa: BLE001
                print(f"[sandbox] aclose failed: {type(e).__name__}: {e}")


app = FastAPI(title="cast-agents", version="0.3.0", lifespan=lifespan)

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
        "version": "0.3.0",
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


def _agent_def_from_hermes(hermes: Hermes, rec: dict[str, Any]) -> AgentDef:
    """Hermes (akong-hermes) → harness AgentDef · skills 用 hermes resolved · 兼容 rec.metadata。

    rec 是 cast-api agents row · 仍旧需要拿 tagline / metadata extras (rules_json 等)。
    skills 优先用 hermes.skills (resolver 解出的 sop) · 兜底到 rec.skills。
    """
    meta = rec.get("metadata_json")
    if isinstance(meta, str):
        try:
            import json as _json
            meta = _json.loads(meta)
        except (ValueError, TypeError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}

    # hermes.skills 是 SkillRef list · runtime.AgentDef.skills 期望 str list (skill name)
    # 取 SkillRef.static_ref 的 :: 后半 · 或 dynamic skill_id · 让老 skill_registry 路径继续走
    skill_names: list[str] = []
    for ref in hermes.skills:
        if ref.kind == "static" and ref.static_ref:
            skill_names.append(ref.static_ref.split("::", 1)[-1])
        elif ref.kind == "dynamic" and ref.skill_id:
            skill_names.append(ref.skill_id)

    return AgentDef(
        id=rec["id"],
        name=hermes.name or rec.get("name") or "",
        tagline=rec.get("tagline") or "",
        soul=hermes.soul or rec.get("soul") or "",
        playbook=hermes.playbook or rec.get("playbook") or "",
        style=hermes.style or rec.get("style") or "",
        skills=skill_names,
        metadata=meta,
    )


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

    # 1b. 优先走新 hermes loader (老板 5-9 拍) · 失败兜底老路径
    agent: AgentDef
    try:
        async with DynamicHermesLoader(settings.api_base_url) as hermes_loader:
            hermes = await hermes_loader.load_by_agent_id(req.agent_id)
        agent = _agent_def_from_hermes(hermes, record)
        print(f"[hermes] /api/agent/run {req.agent_id} loaded hermes={hermes.id} static_ref={hermes.static_ref}")
    except KeyError:
        # 该 agent 还没 hermes 行 (过渡期) · 走老路径
        agent = _agent_def_from_record(record)
        print(f"[hermes] /api/agent/run {req.agent_id} no hermes row · fallback to record")
    except Exception as e:  # noqa: BLE001 · loader 失败不能 crash
        print(f"[hermes] WARN: load_by_agent_id failed: {type(e).__name__}: {e} · fallback to record")
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
