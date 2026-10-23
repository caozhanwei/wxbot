# -*- coding: utf-8 -*-
"""R3 未解问题闭环：#催办 N 群里问谁在跟 → #已解 N 答案归档喂主题库。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import followup as followup_mod
from wxbot import themes as themes_mod
from wxbot.gate import Gate
from wxbot.store import Store


class _Driver:
    self_id = "wxid_bot"
    self_name = "智能机器人"

    def display_of(self, uid):
        return uid


def _mk_bot(tmp_path, groups=None):
    bot = type("B", (), {
        "store": Store(str(tmp_path / "t.db")),
        "driver": _Driver(),
        "personas": type("P", (), {})(),
        "state": {},
        "cfg": type("C", (), {"data": {
            "app": {"gate": {"enabled": False}},
            "groups": groups if groups is not None else [
                {"id": "111@chatroom", "display": "测试群", "enabled": True}],
        }})(),
        "watch": [{"user": "111@chatroom", "display": "测试群",
                   "kind": "group", "cfg": {}}],
    })()
    bot.gate = Gate(bot)
    bot.sent = []

    def _send(display, text, kind="reply"):
        bot.sent.append((display, text, kind))
        return True
    bot.send = _send
    return bot


def _mk_pending(bot, grp="测试群", q="门禁卡哪里办", asker="阿强"):
    bot.store.add_qa_pending(grp, asker, q)
    return bot.store.qa_pending()[0]["id"]


# ---------- 催办 ----------

def test_urge_sends_to_group(tmp_path):
    bot = _mk_bot(tmp_path)
    rid = _mk_pending(bot)
    out = followup_mod.cmd_urge(bot, [str(rid)])
    assert "测试群" in out or "已催" in out
    assert len(bot.sent) == 1
    display, text, kind = bot.sent[0]
    assert display == "测试群" and kind == "followup"
    assert "门禁卡" in text and "跟进" in text


def test_urge_unknown_group(tmp_path):
    bot = _mk_bot(tmp_path, groups=[
        {"id": "111@chatroom", "display": "别的群", "enabled": True}])
    rid = _mk_pending(bot, grp="找不到的群")
    out = followup_mod.cmd_urge(bot, [str(rid)])
    assert bot.sent == [] and ("找不到" in out or "没有" in out)


# ---------- 已解归档 ----------

def test_resolve_feeds_theme_lib(tmp_path, monkeypatch):
    bot = _mk_bot(tmp_path)
    rid = _mk_pending(bot)
    assigned = []
    monkeypatch.setattr(themes_mod, "assign_async",
                        lambda b, g: assigned.append(g))
    out = followup_mod.cmd_resolve(bot, [str(rid), "物业前台带身份证"])
    assert "已解" in out or "归档" in out
    assert bot.store.get_qa_pending(rid)["status"] == "done"
    pairs = bot.store.mined_pairs("111@chatroom")
    assert pairs and pairs[0]["question"] == "门禁卡哪里办"
    assert pairs[0]["answer"] == "物业前台带身份证"
    assert assigned == ["111@chatroom"]


def test_resolve_group_not_in_config(tmp_path):
    bot = _mk_bot(tmp_path, groups=[])
    rid = _mk_pending(bot)
    out = followup_mod.cmd_resolve(bot, [str(rid), "答案"])
    assert bot.store.get_qa_pending(rid)["status"] == "done"
    assert bot.store.mined_pairs("111@chatroom") == []   # 找不到群不入库
    assert "配置" in out or "找不到" in out


def test_resolve_missing_answer(tmp_path):
    bot = _mk_bot(tmp_path)
    rid = _mk_pending(bot)
    out = followup_mod.cmd_resolve(bot, [str(rid)])
    assert bot.store.get_qa_pending(rid)["status"] == "pending"
    assert "答案" in out
