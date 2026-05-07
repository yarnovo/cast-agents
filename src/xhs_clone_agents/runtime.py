"""agent loop · 一次 wakeup 的完整流程"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from anthropic import Anthropic

from .config import settings
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
    llm = Anthropic(api_key=settings.anthropic_api_key)

    system = _build_system_prompt(ws)
    messages: list[dict] = [{"role": "user", "content": _build_user_prompt(ws, trigger or {"kind": "cron"})}]
    actions: list[dict] = []

    try:
        for _ in range(MAX_TURNS):
            resp = llm.messages.create(
                model=settings.anthropic_model,
                max_tokens=2048,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                break

            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            stop = False
            for tu in tool_uses:
                try:
                    result = execute_tool(tu.name, tu.input or {}, ws, client)
                    actions.append({"tool": tu.name, "input": tu.input, "result": result})
                    if tu.name == "stop_for_now":
                        stop = True
                except Exception as e:
                    actions.append({"tool": tu.name, "input": tu.input, "error": str(e)})
                    result = {"error": str(e)}
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(result)[:4000]})

            messages.append({"role": "user", "content": tool_results})
            if stop:
                break
    finally:
        client.close()

    ws.set_state(last_tick_at=datetime.now(UTC).isoformat())
    return {"agent": agent_name, "actions": actions, "turns": len([m for m in messages if m["role"] == "assistant"])}
