# -*- coding: utf-8 -*-
"""私聊历史冷启动：滑窗迭代提炼联系人长期记忆。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import bootstrap
from wxbot.store import Store


class FakeDB:
    def __init__(self, msgs):
        self.msgs = msgs

    def get_messages(self, user, limit=20, offset=0):
        return list(self.msgs[:limit])


class FakeDriver:
    def __init__(self, msgs, self_id="wxid_me"):
        self.db = FakeDB(msgs)
        self.self_id = self_id

    def resolve_id(self, display):
        return {"林昆": "wxid_lin"}.get(display, "")

    def display_of(self, uid):
        return {"wxid_lin": "林昆", "wxid_me": "我"}.get(uid, uid)


class FakePersonas:
    def __init__(self):
        self.calls = []

    def extract_contact_memory(self, who, lines, current=""):
        self.calls.append(list(lines))
        return f"记忆(共{len(self.calls)}轮): 关心买车与孩子上学"


def _mk(sender, text):
    return {"sender_username": sender, "content": text}


def _bot(tmp_path, msgs):
    class B:
        driver = FakeDriver(msgs)
        personas = FakePersonas()
        store = Store(str(tmp_path / "t.db"))

    return B()


def test_contact_memory_mining_merges_chunks(tmp_path):
    msgs = ([_mk("wxid_me", "我说的%d" % i) for i in range(8)]
            + [_mk("wxid_lin", "对方说的%d" % i) for i in range(8)]
            + [_mk("wxid_lin", "银行卡号 6222 0000 0000")]   # 敏感跳过
            + [_mk("wxid_lin", "<?xml><sysmsg/>")])          # 系统消息跳过
    bot = _bot(tmp_path, msgs)
    r = bootstrap.run_contact_memory_sync(bot, "林昆")
    assert "error" not in r
    assert r["wxid"] == "wxid_lin"
    assert r["lines"] == 16
    # 16 行按 10 行/窗 → 2 轮合并
    assert len(bot.personas.calls) == 2
    assert bot.store.get_contact_memory("wxid_lin").startswith("记忆(共2轮)")
    assert "我说的" in "\n".join(bot.personas.calls[0])  # 双方发言都进了窗口


def test_contact_memory_mining_resolve_fail(tmp_path):
    bot = _bot(tmp_path, [])
    r = bootstrap.run_contact_memory_sync(bot, "不存在的人")
    assert "error" in r


def test_contact_memory_mining_empty_history(tmp_path):
    bot = _bot(tmp_path, [])
    r = bootstrap.run_contact_memory_sync(bot, "林昆")
    assert "error" not in r and r["lines"] == 0
    assert bot.personas.calls == []
