# cast-agents

「阿空小造」(meta-agent) 后端 · 帮 Cast 平台用户 (owner) 在几轮对话里造一个属于自己的虚拟角色。

## 定位

Cast = C2A2C 平台 · owner 跟阿空小造聊几句 · 阿空小造收集人设 / 服务包 / 文风 ·
信息够了一次性调 cast-api 的 `POST /api/agents` 把虚拟角色档案存到 RDS · 完工。

仅一个对外 endpoint:

```
POST /api/meta-agent/chat
{ "owner_id": "u_xxx", "history": [...], "message": "..." }
→ { "reply": "...", "created_agent_id": "...|null", "done": true|false }
```

无 cron · 无 webhook · 无 per-user runtime · 纯对话驱动 · 无状态 (history 由前端传)。

## 跑

```bash
# 1. 装依赖
uv sync

# 2. 配 LLM key + cast-api 地址
export DASHSCOPE_API_KEY=sk-...           # 阿里百炼
export LLM_API_KEY=$DASHSCOPE_API_KEY
export API_BASE_URL=http://127.0.0.1:8000  # 本地起 cast-api 时
# prod / staging 走默认 https://api.cast.agentaily.com

# 3. 起服务
uv run uvicorn cast_agents.main:app --reload --port 8001

# 4. 试一轮对话
curl -X POST http://127.0.0.1:8001/api/meta-agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"owner_id":"u_test","history":[],"message":"我想做一个独立设计师的虚拟角色"}'
```

## 测

```bash
uv run pytest -v
```

## 模块

```
src/cast_agents/
├── main.py     FastAPI app · 单 endpoint /api/meta-agent/chat
├── meta.py     MetaAgent · SYSTEM prompt + 3 tool (ask_owner / summarize_and_confirm / create_user_agent)
├── llm.py      OpenAI 兼容客户端 (走阿里百炼 DashScope · DeepSeek-v3.1)
├── config.py   pydantic-settings · LLM_*, API_BASE_URL, ENV
└── __init__.py
```

## 部署

- FC v3 Custom Container (`python:3.13-slim` + uv + uvicorn)
- ACR 镜像 + GHA staging/prod 双 workflow
- prod: `agents.api.cast.agentaily.com`
- staging: `staging.agents.api.cast.agentaily.com`
- LLM_API_KEY 走 FC env (secret manager)
