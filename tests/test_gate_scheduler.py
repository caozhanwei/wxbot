# -*- coding: utf-8 -*-
"""R1：gate.py 动作闸门（频控/时段窗/急停/审计）+ scheduler.py 日任务（槽点/补跑/幂等）。"""
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import gate as gate_mod
from wxbot import scheduler as sched_mod
from wxbot.store import Store


def _mk_gate(tmp_path, **cfg):
    s = Store(str(tmp_path / "t.db"))
    bot = type("B", (), {
        "store": s, "state": {},
        "cfg": type("C", (), {"data": {"app": {"gate": cfg}} if cfg else {}})(),
    })()
    return gate_mod.Gate(bot), s


def test_gate_allows_in_window(tmp_path):
    g, s = _mk_gate(tmp_path)
    ok, why = g.allow("digest", "文件传输助手", "日报", now=dt.datetime(2026, 10, 23, 21, 0).timestamp())
    assert ok is True
    # 审计落库
    rows = [r for r in s.recent_logs(10) if r["kind"] == "gate"]
    assert rows and rows[0]["chat"] == "文件传输助手"


def test_gate_blocks_out_of_window_but_urgent_exempt(tmp_path):
    g, _ = _mk_gate(tmp_path)
    night = dt.datetime(2026, 10, 23, 23, 30).timestamp()
    ok, why = g.allow("digest", "文件传输助手", "日报", now=night)
    assert ok is False and "时段" in why
    ok, _ = g.allow("urgent", "文件传输助手", "急件", urgent=True, now=night)
    assert ok is True


def test_gate_stop_kills_everything(tmp_path):
    g, _ = _mk_gate(tmp_path)
    g.stop()
    noon = dt.datetime(2026, 10, 23, 12, 0).timestamp()
    ok, why = g.allow("digest", "x", "y", now=noon)
    assert ok is False and "急停" in why
    ok, _ = g.allow("urgent", "x", "y", urgent=True, now=noon)
    assert ok is False                     # 急停连急件也拦
    g.resume()
    ok, _ = g.allow("digest", "x", "y", now=noon)
    assert ok is True


def test_gate_hourly_cap(tmp_path):
    g, _ = _mk_gate(tmp_path, hourly_max=2)
    t = dt.datetime(2026, 10, 23, 12, 0).timestamp()
    assert g.allow("digest", "a", "1", now=t)[0]
    assert g.allow("digest", "b", "2", now=t)[0]
    ok, why = g.allow("digest", "c", "3", now=t)
    assert ok is False and "频控" in why
    # 下一小时窗口重置
    ok, _ = g.allow("digest", "c", "3", now=t + 3600)
    assert ok is True


def test_gate_per_chat_daily_cap(tmp_path):
    g, _ = _mk_gate(tmp_path, per_chat_daily_max=2)
    t = dt.datetime(2026, 10, 23, 12, 0).timestamp()
    assert g.allow("digest", "群甲", "1", now=t)[0]
    assert g.allow("digest", "群甲", "2", now=t + 60)[0]
    ok, why = g.allow("digest", "群甲", "3", now=t + 120)
    assert ok is False and "频控" in why
    # 别的会话不受影响；同会话次日重置
    assert g.allow("digest", "群乙", "x", now=t + 180)[0]
    nxt = dt.datetime(2026, 10, 24, 12, 0).timestamp()
    assert g.allow("digest", "群甲", "x", now=nxt)[0]


# ---------- scheduler ----------

D1 = dt.datetime(2026, 10, 23, 12, 0)      # 基准：23 号正午


def _ts(h, m=0, day=23):
    return dt.datetime(2026, 10, day, h, m).timestamp()


def test_due_slot_semantics():
    job = sched_mod.DailyJob("digest", hour=21)
    assert sched_mod.due(job, now=_ts(20)) is False            # 没到点
    assert sched_mod.due(job, now=_ts(21, 5)) is True          # 过点未跑
    assert sched_mod.due(job, now=_ts(21, 5), ran_today=True) is False
    assert sched_mod.due(job, now=_ts(23, 58)) is True         # 当天补跑


def test_due_no_catchup_next_day():
    job = sched_mod.DailyJob("digest", hour=21)
    # 24 号凌晨 1 点：23 号的槽已过但跨天不补，24 号 21 点未到
    assert sched_mod.due(job, now=_ts(1, 0, day=24)) is False
    assert sched_mod.due(job, now=_ts(21, 30, day=24)) is True


def test_scheduler_runs_and_marks(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    ran = []
    bot = type("B", (), {"store": s,
                         "state": {},
                         "cfg": type("C", (), {"data": {"app": {
                             "daily_digest": {"hour": 21}}}})()})()
    job = sched_mod.DailyJob("t", hour=12, fn=lambda b: ran.append(1))
    sch = sched_mod.Scheduler(bot, [job])
    sch.tick(now=_ts(12, 1))
    assert ran == [1]
    assert s.daily_ran("t", "2026-10-23")
    sch.tick(now=_ts(12, 2))                                   # 幂等
    assert ran == [1]


def test_scheduler_exception_isolated(tmp_path):
    s = Store(str(tmp_path / "t.db"))

    def _boom(b):
        raise RuntimeError("炸了")

    bot = type("B", (), {"store": s, "state": {},
                         "cfg": type("C", (), {"data": {"app": {}}})()})()
    ok_job = []
    sch = sched_mod.Scheduler(bot, [
        sched_mod.DailyJob("bad", hour=12, fn=_boom),
        sched_mod.DailyJob("good", hour=12, fn=lambda b: ok_job.append(1)),
    ])
    sch.tick(now=_ts(12, 1))
    assert ok_job == [1]                       # bad 挂了不影响 good
    assert s.daily_ran("bad", "2026-10-23")    # 失败也标记，不反复重试刷屏
