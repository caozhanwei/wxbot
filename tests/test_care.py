# -*- coding: utf-8 -*-
"""R3 主动关怀：距上次聊天超阈值的好友生成问候；护栏（周频控/日上限/时段/急停/dry_run）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import care as care_mod
from wxbot.gate import Gate
from wxbot.store import Store

NOW = time.time()
DAY = 86400


class _DB:
    def __init__(self, last_ts):
        self.last_ts = last_ts

    def get_messages(self, user, limit=500, offset=0):
        return [{"sort_seq": 99, "create_time": self.last_ts.get(user, 0),
                 "sender_username": user, "sender_nickname": user,
                 "type": "文本", "content": "最近一条"}]


class _Driver:
    self_id = "wxid_bot"
    self_name = "智能机器人"

    def __init__(self, last_ts):
        self.db = _DB(last_ts)

    def display_of(self, uid):
        return {"wxid_lin": "林哥", "wxid_wu": "吴总"}.get(uid) or uid


class _LLM:
    def care_greeting(self, who, memory, days):
        return f"{who}，上次聊到你说的{memory[:6]}，后来弄好没？"


def _mk_bot(tmp_path, app_care=None, last_ts=None, gate_stop=False):
    last_ts = last_ts or {"wxid_lin": NOW - 9 * DAY, "wxid_wu": NOW - DAY}
    bot = type("B", (), {
        "store": Store(str(tmp_path / "t.db")),
        "driver": _Driver(last_ts),
        "personas": _LLM(),
        "state": {"gate_stop": gate_stop},
        "cfg": type("C", (), {"data": {
            "app": {"gate": {"enabled": False},
                    **({"care": app_care} if app_care is not None else {})},
        }})(),
        "watch": [
            {"user": "wxid_lin", "display": "林哥", "kind": "contact",
             "cfg": {"id": "wxid_lin", "display": "林哥", "enabled": True}},
            {"user": "wxid_wu", "display": "吴总", "kind": "contact",
             "cfg": {"id": "wxid_wu", "display": "吴总", "enabled": True}},
            {"user": "wxid_zz", "display": "已停用", "kind": "contact",
             "cfg": {"id": "wxid_zz", "display": "已停用", "enabled": False}},
        ],
    })()
    bot.gate = Gate(bot)
    bot.sent = []

    def _send(display, text, kind="reply"):
        bot.sent.append((display, text, kind))
        return True
    bot.send = _send
    return bot


def test_care_disabled_by_default(tmp_path):
    bot = _mk_bot(tmp_path)                             # app.care 未配=关
    assert care_mod.run_care(bot) == 0
    assert bot.sent == []


def test_care_dry_run_default(tmp_path):
    bot = _mk_bot(tmp_path, app_care={"enabled": True})  # dry_run 默认开
    assert care_mod.run_care(bot) == 1
    assert bot.sent == []                                # 只演练不发送
    logs = [r for r in bot.store.recent_logs(20) if r["kind"] == "care_dry"]
    assert logs and "林哥" in logs[0]["detail"]
    assert not bot.store.care_count_since("wxid_lin", 7)  # 演练不占周配额


def test_care_real_send(tmp_path):
    bot = _mk_bot(tmp_path, app_care={"enabled": True, "dry_run": False})
    bot.store.upsert_contact_memory("wxid_lin", "家里装修，关心瓷砖进度")
    assert care_mod.run_care(bot) == 1
    assert len(bot.sent) == 1
    display, text, kind = bot.sent[0]
    assert display == "林哥" and kind == "care" and "装修" in text
    assert bot.store.care_count_since("wxid_lin", 7) == 1
    assert not [r for r in bot.store.recent_logs(20) if r["kind"] == "care_dry"]


def test_care_weekly_limit(tmp_path):
    bot = _mk_bot(tmp_path, app_care={"enabled": True, "dry_run": False})
    bot.store.log_care("wxid_lin", "林哥", "上周刚问候过")
    assert care_mod.run_care(bot) == 0                   # 本周已问候，跳过


def test_care_daily_cap(tmp_path):
    bot = _mk_bot(tmp_path, app_care={"enabled": True, "dry_run": False,
                                      "days_min": 1})
    for i in range(3):
        bot.store.log_care(f"wxid_x{i}", "别人", "今日额度")
    assert care_mod.run_care(bot) == 0                   # 今日 3 条用完


def test_care_gate_stopped(tmp_path):
    bot = _mk_bot(tmp_path, app_care={"enabled": True, "dry_run": False},
                  gate_stop=True)
    assert care_mod.run_care(bot) == 0
    assert bot.sent == []


def test_care_days_threshold(tmp_path):
    bot = _mk_bot(tmp_path, app_care={"enabled": True, "dry_run": True,
                                      "days_min": 30})
    assert care_mod.run_care(bot) == 0                   # 9 天 < 30 天，没人够格
