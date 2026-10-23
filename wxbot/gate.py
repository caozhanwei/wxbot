# -*- coding: utf-8 -*-
"""动作闸门 ActionGate：所有"机器人主动发起"的动作统一过闸。

护栏顺序：急停 > 时段窗（急件豁免）> 全局每小时上限 > 每会话每日上限；
放行即计数并落 store 日志（kind=gate）审计。计数在内存，重启清零
（重启本来就是罕见事件，损失一点配额精度换零额外 DB 写）。
"""
import logging
import threading
import time
from datetime import datetime

log = logging.getLogger("wxbot.gate")

_DEFAULTS = {"enabled": True, "hourly_max": 20, "per_chat_daily_max": 30,
             "window": [8, 22]}


class Gate:
    def __init__(self, bot):
        self.bot = bot
        cfg = ((bot.cfg.data.get("app") or {}).get("gate")) or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.hourly_max = int(cfg.get("hourly_max") or _DEFAULTS["hourly_max"])
        self.per_chat_daily = int(cfg.get("per_chat_daily_max")
                                  or _DEFAULTS["per_chat_daily_max"])
        win = cfg.get("window") or _DEFAULTS["window"]
        try:
            self.win_start, self.win_end = int(win[0]), int(win[1])
        except Exception:
            self.win_start, self.win_end = 8, 22
        self._lock = threading.Lock()
        self._hour = {}      # hour_bucket -> 次数
        self._day_chat = {}  # (day, chat) -> 次数

    @property
    def stopped(self):
        return bool(self.bot.state.get("gate_stop"))

    def stop(self):
        self.bot.state["gate_stop"] = True
        log.warning("动作闸门：急停已生效（全部主动动作拦截）")

    def resume(self):
        self.bot.state["gate_stop"] = False
        log.info("动作闸门：急停已解除")

    def allow(self, key, chat, text="", urgent=False, now=None):
        """返回 (ok, reason)。allowed=True 时计数并写审计日志。"""
        now = time.time() if now is None else now
        if self.stopped:
            return False, "急停中"
        if self.enabled and not urgent:
            h = datetime.fromtimestamp(now).hour
            if not (self.win_start <= h < self.win_end):
                return False, f"时段窗外({self.win_start}-{self.win_end}点)"
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        bucket = int(now // 3600)
        with self._lock:
            n_hour = self._hour.get(bucket, 0) + 1
            n_chat = self._day_chat.get((day, chat), 0) + 1
            if self.hourly_max > 0 and n_hour > self.hourly_max:
                return False, f"频控:全局每小时超限({self.hourly_max})"
            if self.per_chat_daily > 0 and n_chat > self.per_chat_daily:
                return False, f"频控:会话每日超限({self.per_chat_daily})"
            self._hour[bucket] = n_hour
            self._day_chat[(day, chat)] = n_chat
            self._hour = {k: v for k, v in self._hour.items() if k >= bucket - 2}
        self.bot.store.log("gate", chat, key, str(text)[:80])
        return True, "ok"
