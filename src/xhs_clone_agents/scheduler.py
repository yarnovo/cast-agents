"""定时叫醒 agent · 兜底 cron + 自定义 next_wakeup"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .workspace import list_agents


_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.start()
    return _scheduler


def schedule_cron_for_all(callback: Callable[[str], None], every_minutes: int = 30) -> None:
    """每 N 分钟扫一遍所有 agent · 叫醒"""
    sch = get_scheduler()
    sch.add_job(
        lambda: [callback(name) for name in list_agents()],
        IntervalTrigger(minutes=every_minutes),
        id="all_agents_cron",
        replace_existing=True,
    )


def schedule_one_shot(callback: Callable[[str], None], agent_name: str, run_at: datetime) -> str:
    sch = get_scheduler()
    job = sch.add_job(callback, DateTrigger(run_date=run_at), args=[agent_name])
    return job.id


def schedule_calendar_entry(callback: Callable[[str, str], None], agent_name: str, when: str, what: str) -> str:
    """从 calendar.md 解析的一条 · ISO 时间或 cron 表达式"""
    sch = get_scheduler()
    if when.startswith("每"):  # 简陋的中文 cron 占位 · MVP 只识别"每天 HH:MM" / "每周 N HH:MM"
        # TODO: 真要做就接 chinese-cron 库 · 现 placeholder
        trig = CronTrigger(hour=7, minute=0)
    else:
        trig = DateTrigger(run_date=datetime.fromisoformat(when))
    job = sch.add_job(callback, trig, args=[agent_name, what])
    return job.id
