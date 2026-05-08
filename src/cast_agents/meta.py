"""meta-agent · 造物主 · 跟真用户对话引导造分身 (走阿里百炼 + DeepSeek)

跟普通 agent 区别:
- 不绑 xhs user_id · 系统级
- 多一个 tool: create_user_agent (调 xhs-clone-api 后端)
- 不走 cron · 只走对话触发 (前端 chat 调 /api/meta-agent/chat)
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import settings
from .llm import new_client, to_openai_tool


SYSTEM_PROMPT = """你是"阿空小造" · 帮人在「Cast」平台造虚拟角色 (digital persona) 的 AI 引导师。

# 你的工作
真人 owner 来跟你聊 · 想造一个属于自己的虚拟角色。这个角色在平台上当 owner 的"专业代言人"——
1. 发笔记分享作品 / 思考 (内容 feed)
2. 接陌生人的私信咨询 (24h 在线)
3. 接付费订单 · 简单的 AI 自己干 · 复杂的转给 owner

你的任务是通过几轮对话 · 帮 owner 想清楚:
- 角色的人设 (年龄 / 职业 / 性格 / 文风)
- 主营服务 (擅长啥 · 客单价 · 交付时间 · AI 还是真人交付)
- 内容方向 (每天发啥)

# 对话原则
- 一次问一个问题 · 不堆问卷
- 用大白话 · 不用术语
- 引导 owner 用具体例子说 (而不是"我擅长设计"这种空泛)
- 关键节点确认 · 不擅自决定
- 收集到足够信息时 · 调 create_user_agent 一次性把档案建好 · 不分多次

# 收集字段 (够了就建)
- name: 角色名 (owner 起的 · 不一定是真名)
- tagline: 一句话介绍 ≤30 字
- soul: 人设详细 (年龄 / 性格 / 故事)
- playbook: 接活规则 + 内容方向
- style: 文风样板 (举 1-2 段他想要的内容例子)
- expertise: 擅长技能列表 + 服务包 (title / 简介 / 价格 / 交付时长 / ai|human|hybrid)
- avatar: 头像 (没的话给个 emoji 占位)

# 不要做的
- 不要直接帮 owner 编故事 · 你是采访 · 不是创作
- 不要塞行业刻板印象 (设计师就要文艺 / 程序员就要理工男)
- 不要承诺平台没承诺的 (退款 / SLA 这些先按 service.sla_hours 走)
"""


META_TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_user_agent",
        "description": "把跟 owner 聊到的所有信息一次性写成虚拟角色档案 · 创建后角色立刻在市场可见",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tagline": {"type": "string"},
                "soul": {"type": "string"},
                "playbook": {"type": "string"},
                "style": {"type": "string"},
                "expertise": {"type": "string"},
                "avatar": {"type": "string", "description": "头像 emoji 或图片 URL · 没的话写 ✨"},
                "services": {
                    "type": "array",
                    "description": "服务包列表 · 0-5 个",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "price_cents": {"type": "integer", "description": "分 · 99 元 = 9900"},
                            "sla_hours": {"type": "integer", "default": 72},
                            "mode": {"type": "string", "enum": ["ai", "human", "hybrid"], "default": "hybrid"},
                        },
                        "required": ["title", "description", "price_cents"],
                    },
                    "default": [],
                },
            },
            "required": ["name", "tagline", "soul", "playbook", "style", "expertise"],
        },
    },
    {
        "name": "ask_owner",
        "description": "需要 owner 回答 · 把问题作为 reply 返回 · 只问一个问题",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "summarize_and_confirm",
        "description": "信息收得差不多了 · 跟 owner 确认整体设定 · 待 owner 说 OK 再 create_user_agent",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]


class MetaAgent:
    """无状态 · 每次 chat() 接收完整 history + new message · 决定下一步"""

    def __init__(self, api_base_url: str | None = None):
        self.api_base = (api_base_url or settings.api_base_url).rstrip("/")
        self.llm = new_client()
        self.tools = [to_openai_tool(t) for t in META_TOOLS]
        self.http = httpx.Client(base_url=self.api_base, timeout=20.0, trust_env=False)

    def close(self) -> None:
        self.http.close()

    def _create_agent_via_api(self, owner_id: str, args: dict) -> dict:
        services = args.pop("services", [])
        r = self.http.post(f"/api/agents?owner_id={owner_id}", json={
            "name": args.get("name", ""),
            "tagline": args.get("tagline", ""),
            "soul": args.get("soul", ""),
            "playbook": args.get("playbook", ""),
            "style": args.get("style", ""),
            "expertise": args.get("expertise", ""),
            "avatar": args.get("avatar", "✨"),
        })
        r.raise_for_status()
        agent = r.json()

        added_services = []
        for svc in services:
            rs = self.http.post(
                f"/api/agents/{agent['id']}/services?owner_id={owner_id}",
                json={
                    "title": svc.get("title", "服务包"),
                    "description": svc.get("description", ""),
                    "price_cents": int(svc.get("price_cents", 9900)),
                    "sla_hours": int(svc.get("sla_hours", 72)),
                    "mode": svc.get("mode", "hybrid"),
                },
            )
            if rs.status_code == 201:
                added_services.append(rs.json())
        return {"agent": agent, "services": added_services}

    def chat(self, owner_id: str, history: list[dict], new_message: str) -> dict:
        """单步对话

        history: [{"role": "user"|"assistant", "content": str}]
        return: {"reply": str, "created_agent_id": str | None, "done": bool}
        """
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history:
            role = m.get("role")
            if role in ("user", "assistant") and m.get("content"):
                messages.append({"role": role, "content": m["content"]})
        messages.append({"role": "user", "content": new_message})

        for _ in range(4):
            resp = self.llm.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                tools=self.tools,
                max_tokens=2048,
            )
            msg = resp.choices[0].message
            tool_calls = msg.tool_calls or []

            if not tool_calls:
                reply = (msg.content or "").strip() or "(无回复)"
                return {"reply": reply, "created_agent_id": None, "done": False}

            # 拼回 assistant message
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })

            reply_to_user: str | None = None
            created_id: str | None = None
            done = False

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name == "ask_owner":
                    reply_to_user = args.get("question", "")
                    tool_result = "asked"
                elif name == "summarize_and_confirm":
                    reply_to_user = args.get("summary", "")
                    tool_result = "summarized"
                elif name == "create_user_agent":
                    try:
                        result = self._create_agent_via_api(owner_id, dict(args))
                        created_id = result["agent"]["id"]
                        reply_to_user = (
                            f"建好啦 · 你的分身「{result['agent']['name']}」已经上市场了 · ID: {created_id}\n"
                            f"配了 {len(result['services'])} 个服务包 · 你可以随时在「我的分身」里再调。"
                        )
                        tool_result = json.dumps({"created": result["agent"]["id"]}, ensure_ascii=False)
                        done = True
                    except Exception as e:
                        reply_to_user = f"建分身时出了点问题 · {e} · 我们再聊聊?"
                        tool_result = json.dumps({"error": str(e)}, ensure_ascii=False)
                else:
                    tool_result = json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False)

                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

            if reply_to_user is not None:
                return {"reply": reply_to_user, "created_agent_id": created_id, "done": done}

        return {"reply": "(我有点卡住了 · 你再说一遍?)", "created_agent_id": None, "done": False}
