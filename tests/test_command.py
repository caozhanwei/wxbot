# -*- coding: utf-8 -*-
"""R4 一句话操控：关键词快路径 + LLM 意图路由 + 白名单动作 + 双入口接线。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import command as command_mod
from wxbot.store import Store

from test_console import _mk_bot

NOW = time.time()
DAY = 86400


class _Router:
    """注入 bot.personas 的意图路由替身。"""

    def __init__(self, ret=None, boom=False):
        self.ret = ret
        self.boom = boom
        self.calls = 0

    def route_intent(self, text, intents_doc="", base_date=""):
        if self.boom:
            raise RuntimeError("LLM 挂了")
        self.calls += 1
        return self.ret


def _bot_with(tmp_path, router):
    b = _mk_bot(tmp_path)
    b.build_watch()
    b.personas = router
    return b


# ---------- 关键词快路径（不碰 LLM） ----------

def test_kw_route_exact_phrases(tmp_path):
    b = _bot_with(tmp_path, _Router(boom=True))          # LLM 一调就炸：命中快路径才不炸
    assert command_mod.route(b, "暂停")["intent"] == "pause"
    assert command_mod.route(b, "停一下")["intent"] == "pause"
    assert command_mod.route(b, "恢复")["intent"] == "resume"
    assert command_mod.route(b, "补报")["intent"] == "missed"
    assert command_mod.route(b, "急停主动")["intent"] == "stop_active"
    assert command_mod.route(b, "状态")["intent"] == "status"
    assert command_mod.route(b, "约定列表")["intent"] == "reminders"
    assert b.personas.calls == 0


def test_kw_route_tolerates_tail(tmp_path):
    b = _bot_with(tmp_path, _Router(boom=True))
    assert command_mod.route(b, "暂停。")["intent"] == "pause"
    assert command_mod.route(b, "补报！")["intent"] == "missed"


def test_plain_question_skips_llm(tmp_path):
    """不像指令的话不浪费一次 LLM 路由，直接走原问答链路。"""
    b = _bot_with(tmp_path, _Router(ret={"intent": "pause"}))
    assert command_mod.route(b, "小区门口的快递柜怎么用呀") is None
    assert b.personas.calls == 0


def test_llm_none_intent_returns_none(tmp_path):
    b = _bot_with(tmp_path, _Router(ret={"intent": "none"}))
    assert command_mod.route(b, "帮我暂停老司机群") is None    # commandish → 问了 LLM
    assert b.personas.calls == 1


def test_llm_unknown_intent_dropped(tmp_path):
    b = _bot_with(tmp_path, _Router(ret={"intent": "reboot"}))
    assert command_mod.route(b, "帮我重启微信") is None          # 白名单外丢弃


def test_llm_failure_returns_none(tmp_path):
    b = _bot_with(tmp_path, _Router(boom=True))
    assert command_mod.route(b, "帮我暂停老司机群") is None


# ---------- 动作执行 ----------

def test_execute_pause_resume_and_gate(tmp_path):
    b = _bot_with(tmp_path, _Router())
    out = command_mod.execute(b, {"intent": "pause"})
    assert b.state["paused"] is True and "已暂停" in out
    out = command_mod.execute(b, {"intent": "resume"})
    assert b.state["paused"] is False and "已恢复" in out
    command_mod.execute(b, {"intent": "stop_active"})
    assert b.gate.stopped is True
    command_mod.execute(b, {"intent": "resume_active"})
    assert b.gate.stopped is False


def test_execute_missed_status_reminders(tmp_path, monkeypatch):
    from wxbot import digest as digest_mod
    from wxbot import reminders as reminders_mod
    monkeypatch.setattr(digest_mod, "report_missed_now", lambda bot: "补报：暂无")
    b = _bot_with(tmp_path, _Router())
    assert command_mod.execute(b, {"intent": "missed"}) == "补报：暂无"
    assert "运行" in command_mod.execute(b, {"intent": "status"})
    b.store.add_reminder("测试", "张三", "交材料", NOW + DAY)
    out = command_mod.execute(b, {"intent": "reminders"})
    assert "张三" in out and "交材料" in out


def test_execute_remind_add(tmp_path):
    b = _bot_with(tmp_path, _Router())
    d = time.strftime("%Y-%m-%d", time.localtime(NOW + DAY))
    out = command_mod.execute(b, {"intent": "remind_add", "who": "张三",
                                  "what": "交材料", "date": d, "time": "10:00"})
    assert "已记下" in out and "张三" in out
    rows = b.store.list_reminders()
    assert len(rows) == 1 and rows[0]["what"] == "交材料"


def test_execute_remind_add_bad_time_and_range(tmp_path):
    b = _bot_with(tmp_path, _Router())
    out = command_mod.execute(b, {"intent": "remind_add", "who": "张三",
                                  "what": "交材料", "date": "", "time": ""})
    assert "时间" in out
    out = command_mod.execute(b, {"intent": "remind_add", "who": "张三",
                                  "what": "交材料", "date": "2020-01-01"})
    assert "60 天" in out


def test_execute_recall(tmp_path, monkeypatch):
    from wxbot import recall as recall_mod
    monkeypatch.setattr(recall_mod, "answer", lambda bot, q: f"查到了({q})")
    b = _bot_with(tmp_path, _Router())
    out = command_mod.execute(b, {"intent": "recall", "query": "停水"})
    assert out == "查到了(停水)"


def test_execute_enable_disable_group(tmp_path):
    b = _bot_with(tmp_path, _Router())
    out = command_mod.execute(b, {"intent": "disable", "target": "测试群"})
    assert "已停用" in out
    assert [g for g in b.cfg.data["groups"] if g["id"] == "111@chatroom"][0]["enabled"] is False
    out = command_mod.execute(b, {"intent": "enable", "target": "测试群"})
    assert "已启用" in out


def test_execute_disable_missing_target(tmp_path):
    b = _bot_with(tmp_path, _Router())
    assert "哪个" in command_mod.execute(b, {"intent": "disable"})


# ---------- 对话台 / 群 双入口 ----------

def test_console_command_entry(tmp_path, monkeypatch):
    from wxbot import recall as recall_mod
    monkeypatch.setattr(recall_mod, "answer", lambda bot, q: "recall不該被调")
    b = _bot_with(tmp_path, _Router())
    w = [x for x in b.watch if x["kind"] == "console"][0]
    m = {"type": "文本", "is_self": True, "sender": "wxid_bot",
         "sender_display": "智能机器人", "seq": 10, "ts": time.time(),
         "content": "暂停"}
    b._on_console_msgs(w, [m])
    assert b.state["paused"] is True
    recs = [r for r in b.store.recent_logs(20) if r["kind"] == "cmd"]
    assert recs and "已暂停" in recs[0]["detail"]


def test_console_non_command_falls_to_recall(tmp_path, monkeypatch):
    from wxbot import recall as recall_mod
    asked = []

    def _answer(bot, q):
        asked.append(q)
        return "小区群里聊过快递柜在东门"
    monkeypatch.setattr(recall_mod, "answer", _answer)
    b = _bot_with(tmp_path, _Router(ret={"intent": "pause"}))
    w = [x for x in b.watch if x["kind"] == "console"][0]
    m = {"type": "文本", "is_self": True, "sender": "wxid_bot",
         "sender_display": "智能机器人", "seq": 11, "ts": time.time(),
         "content": "群里谁聊过快递柜"}
    b._on_console_msgs(w, [m])
    assert asked == ["群里谁聊过快递柜"]
    assert b.personas.calls == 0                       # 不像指令没问路由


def _at_msg(text, sender="wxid_admin", name="管理员", seq=100):
    return {"type": "文本", "is_self": False, "sender": sender,
            "sender_display": name, "seq": seq, "ts": time.time(),
            "content": f"@智能机器人 {text}"}


def test_group_admin_command(tmp_path, monkeypatch):
    from wxbot import digest as digest_mod
    monkeypatch.setattr(digest_mod, "report_missed_now", lambda bot: "补报：暂无")
    b = _bot_with(tmp_path, _Router(boom=True))        # 走关键词快路径即可
    w = [x for x in b.watch if x["kind"] == "group"][0]
    b.state["paused"] = False
    assert b._maybe_command(w, _at_msg("补报"), "补报") is True
    recs = [r for r in b.store.recent_logs(20) if r["kind"] == "cmd"]
    assert recs and "补报：暂无" in recs[0]["detail"]


def test_group_non_admin_not_intercepted(tmp_path):
    b = _bot_with(tmp_path, _Router(ret={"intent": "pause"}))
    w = [x for x in b.watch if x["kind"] == "group"][0]
    m = _at_msg("补报", sender="wxid_stranger", name="路人甲")
    assert b._maybe_command(w, m, "补报") is False
    assert b.personas.calls == 0                       # 非管理员根本不进路由
