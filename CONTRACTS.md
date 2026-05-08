# cast-agents · 上下游契约

本仓 = cast 平台 agent 后端**装配商** · FastAPI 服务 · FC v3 部署。
改本仓任何 endpoint shape / 依赖版本 · 都先扫这份 CONTRACTS · 跟下游对账。

## 1. 上游 (本仓 import / 调)

| 上游仓 | 协议 | 本仓依赖点 | 改这里影响本仓 |
|---|---|---|---|
| `akong-runtime` (git@main) | Python lib | `from akong_runtime import AgentDef, Trigger, run, tick, TickResult, RunResult` | `tick / run` API 变 / `AgentDef / RunResult` 字段变 → 本仓 `main.py` 同步改 |
| `akong-llm` (git@main) | Python lib | `from akong_llm import LLMError, OpenAICompatibleClient, ChatResponse, ToolCall, Usage` | `OpenAICompatibleClient` 构造参数变 → 本仓 `main.py` 同步 |
| `akong-session` (git@main) | Python lib | `from akong_session import RdsSession, SessionUnavailable` | `RdsSession` 构造参数变 → 本仓 `main.py` 同步 |
| `akong-memory` (git@main) | Python lib | `from akong_memory import RdsAdapter` | `RdsAdapter` 构造参数变 → 本仓 `main.py` 同步 |
| `akong-tools` (git@main) | Python lib | `from akong_tools import Tools, ToolSpec, all_registered_tools` (test) · `register_tool` 注册中心被 cast-platform-tools 用 | `Tools.connect` 签名变 / registry API 变 → 本仓 + cast-tools 同步 |
| `akong-skills` (git@main) | Python lib | `from akong_skills import default_registry as default_skill_registry` | skill registry 工厂签名变 → 本仓 `main.py` 同步 |
| `cast-platform-tools` (git@main) | Python lib · import 即 register tool | `import cast_platform_tools` 触发 `register_tool("cast.post" / "cast.send_dm" / "cast.like_post" / "cast.follow_user" / "cast.create_agent")` | 5 tool 名字 / 签名 / 注册时机变 → 本仓 `test_main_smoke.py` 失败 |
| `cast-api` (https://api.cast.agentaily.com · prod / staging.api.cast.agentaily.com) | HTTP | `GET /api/agents/{id}` (拉 agent row · 喂 AgentDef) · `POST/GET/DELETE /api/chat_messages` (RdsSession 持久化 · session_id sticky) · `POST /api/agents` 等 (builtin sync) | 任一 endpoint 删 / shape 改 · 本仓 endpoint 全挂 (尤其 `/api/agent/run` · `/api/agent/tick` · 启动 lifespan builtin sync) |
| `cast-builtin-agents` (git@main) | YAML 真源 · build context | Dockerfile COPY · `BUILTIN_DIR` env override | 改 yaml schema → builtin_sync.py 同步改 |
| LLM provider (DashScope · OpenAI 兼容) | HTTP | `OpenAICompatibleClient(base_url=AKONG_LLM_BASE_URL, model=AKONG_LLM_MODEL, api_key=AKONG_LLM_API_KEY)` | API key revoke / endpoint 切 · 改 FC 函数 env 变量 |

## 2. 下游 (调本仓的)

| 下游 | 调本仓哪个 endpoint | 注意 |
|---|---|---|
| `cast-app` (m.cast.agentaily.com / staging.m.cast.agentaily.com) | 现链路: `POST /api/agent/tick` (CreateRolePage 用 · meta agent 阿空小造) · 后续会切到 `POST /api/agent/run` (多轮 tool use · session sticky) | 本仓不能删 `/api/agent/tick` · 不能改 TickResult shape · 切换前 cast-app 做兼容 |

## 3. 对外承诺 endpoint shape (本仓的 API)

```
GET  /                  → {name, version, env, description}
GET  /health            → {status: "ok", env}
POST /api/agent/tick    body  { agent_id: str, trigger: { kind: str, payload?: dict } }
                        resp  { actions, messages, next_wakeup, stopped, error, [trace?] }
POST /api/agent/run     body  { agent_id: str, session_id: str, user_message: str, max_turns?: int=10 }
                        resp  { messages, final_text, stop_reason, turns_used, actions, usage, error }
                        codes 200 ok · 404 agent 不存在 · 422 body 校验 · 502 LLMError ·
                              503 SessionUnavailable / cast-api unreachable · 500 unexpected
```

## 4. env 真源 (FC 函数 env)

| env | 默认 | 用处 |
|---|---|---|
| `AKONG_LLM_API_KEY` | (必配) | DashScope key · `OpenAICompatibleClient` |
| `AKONG_LLM_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | LLM provider OpenAI 兼容 endpoint |
| `AKONG_LLM_MODEL` | `deepseek-v3.1` | LLM 模型名 |
| `AKONG_API_BASE_URL` (老) / `API_BASE_URL` (新) | `https://api.cast.agentaily.com` | cast-api endpoint · `settings.api_base_url` 读 |
| `CAST_BUILTIN_AGENTS_DIR` | `/app/cast-builtin-agents` (容器内) / `~/.claude/repos/cast/builtin-agents` (dev) | builtin agent yaml 真源 |
| `ENV` | `prod` | 环境标记 (lifespan log 出来) |

## 5. 测试桩 (本仓 mock 上游的 pattern)

- `tests/test_agent_run.py` 用 `httpx.MockTransport` mock cast-api `/api/agents/{id}` + `/api/chat_messages` (跟 `akong-session/tests/test_session.py` 同形态)
- `LLMClient` 用 `_ScriptedLLM` 注入 (跟 `akong-runtime/tests/test_run.py` 同形态)
- `Tools` 用 `_StaticTools` 子类化 (跳过 cast-api tools fetch)
- 不调真 LLM · 不调真 cast-api · 不调真 OSS / NAS

## 6. 改动影响链路 (改本仓后扫这条)

- 改 `/api/agent/run` shape → cast-app 后续接入要同步 (即将切链路)
- 改 `OpenAICompatibleClient` import 来源 → akong-llm 升级要 verify import 还在
- 改 `RdsSession` 构造参数 → akong-session 升级要看 session.py 签名
- 改 cast-api endpoint URL → 同步 README + .env.example + GHA workflow env
- 砍 `/api/agent/tick` 路由 → cast-app /create 页 500 (P0 · 须等 cast-app 切完)
