"""agent loop · 一次 wakeup 的完整流程 · LLM 走阿里百炼 OpenAI 兼容协议"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from typing import Any

from .config import settings
from .llm import new_client, to_openai_tool
from .tools import TOOL_SCHEMAS, XhsClient, execute_tool
from .workspace import Workspace, load_workspace

MAX_TURNS = 8


def _build_system_prompt(ws: Workspace) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""你是一个小红书账号背后的 agent · 用 tools 操作账号。今天是 {today}。

# 你的人设 (soul)
{ws.soul or '(未定义 · 走默认友好风格)'}

# 你的运营守则 (playbook)
{ws.playbook or '(未定义 · 自由发挥但保持人设)'}

# 你的文风样板 (style · 写笔记时模仿)
{ws.style or '(未定义)'}

# 你的长期记忆 (memory · 越下面越新)
{ws.memory or '(空 · 第一次跑)'}

# 你的日历 (calendar · 写过的提醒)
{ws.calendar or '(空)'}

# 行动原则
- 不要每轮都发笔记 · 看 playbook · 适度浏览 / 互动
- 写笔记 / 评论必须跟人设跟文风一致 · 第一人称
- 评论尽量真诚 · 不刷"+1 / 关注我"这种垃圾
- 看到值得记的事 · 调 update_memory · 把它写进长期记忆
- 想给自己留事项 · 调 schedule
- 干完所有想干的事 · 调 set_next_wakeup 告诉 runtime 你下一次想几点醒
- 真没事干 · 调 stop_for_now 收工 (但尽量做点啥)
"""


def _build_user_prompt(ws: Workspace, trigger: dict) -> str:
    return f"""现在叫醒你的原因 (trigger):
{trigger}

你的 xhs user_id: {ws.user_id or '(未注册 · 第一次跑请先注册账号 · 之后存进 state)'}
本次思考完后请按"先 update_memory + schedule (如需) → 干主事 (浏览 / 发笔记 / 评论 / 私信) → set_next_wakeup → stop_for_now"的顺序调用 tools。
"""


def tick(agent_name: str, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
    """跑一次 agent · 返回执行清单"""

    ws = load_workspace(agent_name)
    client = XhsClient(user_id=ws.user_id)
    llm = new_client()
    tools = [to_openai_tool(t) for t in TOOL_SCHEMAS]

    messages: list[dict] = [
        {"role": "system", "content": _build_system_prompt(ws)},
        {"role": "user", "content": _build_user_prompt(ws, trigger or {"kind": "cron"})},
    ]
    actions: list[dict] = []

    try:
        for _ in range(MAX_TURNS):
            resp = llm.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                tools=tools,
                max_tokens=2048,
            )
            msg = resp.choices[0].message
            tool_calls = msg.tool_calls or []

            if not tool_calls:
                break

            # 把 assistant 消息原样拼回去 (含 tool_calls)
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })
            stop = False
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    result = execute_tool(name, args, ws, client)
                    actions.append({"tool": name, "input": args, "result": result})
                    if name == "stop_for_now":
                        stop = True
                except Exception as e:
                    result = {"error": str(e)}
                    actions.append({"tool": name, "input": args, "error": str(e)})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False)[:4000],
                })

            if stop:
                break
    finally:
        client.close()

    ws.set_state(last_tick_at=datetime.now(UTC).isoformat())
    return {
        "agent": agent_name,
        "actions": actions,
        "turns": sum(1 for m in messages if m.get("role") == "assistant"),
    }
