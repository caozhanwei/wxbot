# -*- coding: utf-8 -*-
"""R3 约定提醒：私聊抽取约定 → 到期推送/群内提醒 → #提醒/#销项。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import reminders as reminders_mod
from wxbot.gate import Gate
from wxbot.store import Store

NOW = time.time()
DAY = 86400


def _row(sender, content, seq, age):
    return {"sort_seq": seq, "create_time": NOW - age,
            "sender_username": sender, "sender_nickname": sender,
            "type": "文本", "content": content}


ROWS = {
    "wxid_lin": [
        _row("wxid_lin", "明天上午九点把材料发我", 11, 3600),
        _row("wxid_bot", "好嘞没问题", 12, 3500),
    ],
}


class _DB:
    def get_messages(self, user, limit=500, offset=0):
        return list(ROWS.get(user, []))[:limit]


class _Driver:
    self_id = "wxid_bot"
    self_name = "智能机器人"
    db = _DB()

    def display_of(self, uid):
        return {"wxid_lin": "林哥", "wxid_bot": "智能机器人"}.get(uid) or uid


class _LLM:
    def __init__(self, items=None, boom=False):
        self.items = items or []
        self.boom = boom
        self.calls = 0

    def extract_reminders(self, lines, base_date):
        if self.boom:
            raise RuntimeError("LLM 挂了")
        self.calls += 1
        return {"items": self.items}


def _mk_bot(tmp_path, llm=None):
    llm = llm or _LLM()
    state = {}
    bot = type("B", (), {
        "store": Store(str(tmp_path / "t.db")),
        "driver": _Driver(),
        "personas": llm,
        "state": state,
        "cfg": type("C", (), {"data": {
            "app": {"gate": {"enabled": False}},
            "contacts": [{"id": "wxid_lin", "display": "林哥", "enabled": True}],
        }})(),
        "watch": [{"user": "wxid_lin", "display": "林哥", "kind": "contact",
                   "cfg": {"id": "wxid_lin", "display": "林哥", "enabled": True}}],
    })()
    bot.gate = Gate(bot)
    bot.sent = []

    def _send(display, text, kind="reply"):
        bot.sent.append((display, text, kind))
        return True
    bot.send = _send
    return bot, llm


def _date_str(offset_days):
    return time.strftime("%Y-%m-%d", time.localtime(NOW + offset_days * DAY))


# ---------- 抽取 ----------

def test_extract_chat_creates_reminder(tmp_path):
    llm = _LLM(items=[{"who": "林哥", "what": "把材料发我",
                       "date": _date_str(1), "time": "09:00"}])
    bot, _ = _mk_bot(tmp_path, llm)
    w = bot.watch[0]
    rows = _DB().get_messages("wxid_lin")
    n = reminders_mod.extract_chat(bot, w, rows)
    assert n == 1
    rems = bot.store.list_reminders()
    assert len(rems) == 1
    r = rems[0]
    assert r["who_name"] == "林哥" and "材料" in r["what"]
    assert abs(r["due"] - (NOW + DAY)) < DAY + 60     # 明天 09:00 附近
    assert r["src"] == "林哥" and not r["grp"]
    # 水位推进到扫描行最大 seq
    assert bot.store.get_remind_seq("wxid_lin") == 12


def test_extract_chat_future_only(tmp_path):
    llm = _LLM(items=[{"who": "林哥", "what": "昨天的事",
                       "date": _date_str(-1), "time": "09:00"},
                      {"who": "林哥", "what": "半年后",
                       "date": _date_str(90), "time": "09:00"}])
    bot, _ = _mk_bot(tmp_path, llm)
    n = reminders_mod.extract_chat(bot, bot.watch[0],
                                   _DB().get_messages("wxid_lin"))
    assert n == 0 and bot.store.list_reminders() == []


def test_extract_chat_llm_failure_keeps_watermark(tmp_path):
    bot, llm = _mk_bot(tmp_path, _LLM(boom=True))
    n = reminders_mod.extract_chat(bot, bot.watch[0],
                                   _DB().get_messages("wxid_lin"))
    assert n == 0 and bot.store.list_reminders() == []
    assert bot.store.get_remind_seq("wxid_lin") == 0   # 不推水位，待重试


def test_maybe_extract_throttled(tmp_path):
    bot, llm = _mk_bot(tmp_path, _LLM(items=[]))
    reminders_mod.maybe_extract(bot)
    first = llm.calls
    assert first >= 1 and bot.state["remind_extract_ts"] > 0
    reminders_mod.maybe_extract(bot)                   # 节流窗口内不重扫
    assert llm.calls == first


# ---------- 到期触发 ----------

def test_fire_due_private_push(tmp_path):
    bot, _ = _mk_bot(tmp_path)
    bot.store.add_reminder("林哥", "林哥", "把材料发我", NOW - 60)
    reminders_mod.fire_due(bot)
    assert len(bot.sent) == 1
    display, text, kind = bot.sent[0]
    assert display == "文件传输助手" and kind == "remind"
    assert "约定提醒" in text and "材料" in text and "林哥" in text
    r = bot.store.list_reminders()[0]
    assert r["fired"] and r["status"] == "pending"     # 发过但未销项
    reminders_mod.fire_due(bot)                        # 同一约定只发一次
    assert len(bot.sent) == 1


def test_fire_due_future_not_sent(tmp_path):
    bot, _ = _mk_bot(tmp_path)
    bot.store.add_reminder("林哥", "林哥", "还没到", NOW + DAY)
    reminders_mod.fire_due(bot)
    assert bot.sent == []


def test_fire_due_group_reminds_in_group(tmp_path):
    bot, _ = _mk_bot(tmp_path)
    bot.store.add_reminder("林哥", "林哥", "群里说好发文档", NOW - 60,
                           grp="111@chatroom", grp_name="测试群")
    reminders_mod.fire_due(bot)
    assert len(bot.sent) == 1
    display, text, kind = bot.sent[0]
    assert display == "测试群" and kind == "remind" and "文档" in text


# ---------- 管理命令 ----------

def test_cmd_list_and_done(tmp_path):
    bot, _ = _mk_bot(tmp_path)
    bot.store.add_reminder("林哥", "林哥", "把材料发我", NOW + DAY)
    bot.store.add_reminder("王五", "王五", "代缴物业费", NOW + 2 * DAY)
    out = reminders_mod.cmd_list(bot)
    assert "材料" in out and "物业费" in out
    out = reminders_mod.cmd_done(bot, ["1"])
    assert "销项" in out or "完成" in out
    assert bot.store.list_reminders(status="pending")[0]["what"] == "代缴物业费"
    assert bot.store.list_reminders(status="done")


# ---------- 主循环节流与主人命令 ----------

def test_maybe_fire_throttled_and_fire_once(tmp_path):
    bot, _ = _mk_bot(tmp_path)
    bot.store.add_reminder("林哥", "林哥", "发材料", NOW - 60)
    reminders_mod.maybe_fire(bot)
    assert len(bot.sent) == 1
    reminders_mod.maybe_fire(bot)                 # 60 秒内不重复巡检
    assert len(bot.sent) == 1
    bot.state["remind_fire_ts"] = 0                # 强制再巡检：fired 过的不重发
    reminders_mod.maybe_fire(bot)
    assert len(bot.sent) == 1


def test_admin_reminder_and_help_cmds(tmp_path):
    from test_console import _mk_bot as _mkb
    b = _mkb(tmp_path)
    b.build_watch()
    for c in ("#提醒", "#销项", "#催办", "#已解"):
        assert c in b._do_admin("帮助", [])
    b.store.add_reminder("林哥", "林哥", "发材料", NOW + DAY)
    out = b._do_admin("提醒", [])
    assert "林哥" in out and "发材料" in out
    rid = b.store.list_reminders()[0]["id"]
    assert "已销项" in b._do_admin("销项", [str(rid)])
    assert not b.store.list_reminders(status="pending")


def test_admin_urge_dispatch(tmp_path):
    from test_console import _mk_bot as _mkb
    b = _mkb(tmp_path)
    b.build_watch()
    b.store.add_qa_pending("测试群", "阿强", "门禁卡哪里办")
    assert "门禁卡" in b._do_admin("催办", [])
    assert "编号得是数字" in b._do_admin("已解", ["abc", "x"])
