# -*- coding: utf-8 -*-
"""通用日任务调度器：把 group_tasks 的"当天槽点"语义泛化给代码内任务。

规则（与 store.due_tasks 一致的防补跑/防重跑语义，外加跨天不补）：
- 今天的槽点没到 → 不跑；
- 槽点已过且今天没跑过、且还在当天 → 跑（错过开机的点补跑，宽限到 23:59）；
- 跨天的漏跑不补（昨天的日报今天早上补发只会打扰，直接等今晚）。
幂等靠 store.daily_runs(job, day)；任务异常也标记已跑，不反复重试刷屏。
"""
import logging
import threading
import time
from datetime import datetime

log = logging.getLogger("wxbot.scheduler")

_TICK = 60          # 巡检间隔（秒）


class DailyJob:
    """name + hour(+minute) 槽点 + fn(bot) 执行体。"""

    def __init__(self, name, hour, minute=0, fn=None):
        self.name = name
        self.hour = int(hour)
        self.minute = int(minute)
        self.fn = fn


def due(job, now=None, ran_today=None):
    """纯函数判定；ran_today 由 Scheduler 传入（便于测试）。"""
    now = time.time() if now is None else now
    dt = datetime.fromtimestamp(now)
    slot = dt.replace(hour=job.hour, minute=job.minute, second=0, microsecond=0)
    if slot.timestamp() > now:
        return False                      # 槽点未到
    if ran_today is None:
        return True
    return not ran_today


class Scheduler:
    """守护线程巡检 daily_runs；tick() 也可由主循环直接驱动（测试/复用）。"""

    def __init__(self, bot, jobs):
        self.bot = bot
        self.jobs = list(jobs or [])
        self._last = 0.0

    def tick(self, now=None):
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        for job in self.jobs:
            try:
                if not due(job, now=now,
                           ran_today=self.bot.store.daily_ran(job.name, day)):
                    continue
                self.bot.store.mark_daily_run(job.name, day)
                log.info("日任务触发 %s（%02d:%02d 槽）", job.name, job.hour, job.minute)
                if job.fn:
                    job.fn(self.bot)
            except Exception as e:
                log.warning("日任务 %s 执行失败: %s", job.name, e)

    def start(self):
        def _loop():
            while True:
                try:
                    self.tick()
                except Exception as e:          # tick 自身异常不杀线程
                    log.warning("scheduler tick 异常: %s", e)
                time.sleep(_TICK)
        threading.Thread(target=_loop, daemon=True, name="wxbot-scheduler").start()
