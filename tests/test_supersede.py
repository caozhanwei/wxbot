# -*- coding: utf-8 -*-
"""@答疑择新（跨批次版）：答题期间同用户又发了新@，改答最新的那条；
不同用户的@不互相顶替；已答过的消息不重复作答。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.bot import Bot
from wxbot.config import BotConfig

NOW = time.time()


def _cfg_text(tmp_path):
    db = str(tmp_path / "t.db").replace("\\", "/")
    return """
app: {dry_run: true, db_file: "%s"}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
admins: [wxid_root]
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    qa: {enabled: true, grounded_reply: true}
""" % db


class FakeDB:
    def get_messages(self, gid, limit=500, offset=0):
        return []


class FakeDriver:
    """poll 按 since 可编程：peek 用 since=Q1.seq 时吐出"迟到的"新@。"""

    def __init__(self):
        self.self_id = "wxid_bot"
        self.self_name = "群助手"
        self.peek_map = {}          # since_seq -> [msgs]

    def baseline(self, user, lookback=50):
        return 0

    def mention_names(self, group_cfg):
        return ["群助手"]

    def display_of(self, uid):
        return {"wxid_a": "阿强", "wxid_bot": "群助手"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return []

    def poll(self, user, since):
        return list(self.peek_map.get(since, [])), since

    def send_text(self, display, text):
        return True, "ok"


class FakeLLM:
    def chat_json(self, system, user, temperature=0.1):
        if "任务解析器" in system:
            return {"is_task": False}
        return {"standalone": "", "should_answer": True}

    def chat(self, system, user, temperature=0.8):
        return "这是答案"


class FakeKB:
    def __init__(self):
        self.asked = []

    def ask(self, question, room=""):
        self.asked.append(question)
        return ("答案：" + question, True, True)

    def search(self, query, top_k=5):
        return ""


def _msg(content, sender="wxid_a", name="阿强", seq=10, ts=None):
    return {"type": "文本", "is_self": False, "sender": sender,
            "sender_display": name, "content": content, "seq": seq,
            "ts": NOW if ts is None else ts, "at_users": ["wxid_bot"]}


@pytest.fixture
def bot(tmp_path):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())
    b._kb = FakeKB()
    b.build_watch()
    b.personas._llm = FakeLLM()
    return b


def _qa_logs(bot):
    return [r for r in bot.store.recent_logs(50) if r["kind"] == "qa"]


def test_newer_same_user_at_supersedes(bot):
    """处理旧@时发现同用户更新的@：旧问题记补报，改答最新。"""
    q1 = _msg("@群助手 旧问题", seq=10)
    q2 = _msg("@群助手 新问题", seq=11)
    bot.driver.peek_map[10] = [q2]           # peek（since=Q1.seq）吐出 Q2
    bot._on_group_msgs(bot.watch[0], [q1])
    assert bot._kb.asked == ["新问题"]
    assert any("新问题" in r["detail"] for r in _qa_logs(bot))
    assert not any("旧问题" in r["detail"] for r in _qa_logs(bot))
    missed = bot.store.missed_unconsumed()
    assert missed and any("旧问题" in (m["content"] or "") for m in missed)


def test_other_user_at_does_not_supersede(bot):
    """别人后来的@不顶替当前用户的提问：各自的问题都要答。"""
    q1 = _msg("@群助手 阿强的问题", sender="wxid_a", name="阿强", seq=10)
    q2 = _msg("@群助手 老白的问题", sender="wxid_b", name="老白", seq=11)
    bot.driver.peek_map[10] = [q2]
    bot._on_group_msgs(bot.watch[0], [q1])
    assert bot._kb.asked == ["阿强的问题"]    # 不被老白顶替
    bot.driver.peek_map.pop(10, None)
    bot._on_group_msgs(bot.watch[0], [q2])    # 老白的问题随后照答
    assert bot._kb.asked == ["阿强的问题", "老白的问题"]


def test_answered_seq_not_reanswered(bot):
    """已答过的@消息（水位重放/重复批次）不再重复作答。"""
    q = _msg("@群助手 就一个问题", seq=10)
    bot._on_group_msgs(bot.watch[0], [q])
    assert bot._kb.asked == ["就一个问题"]
    bot._on_group_msgs(bot.watch[0], [q])     # 同一条再来一遍
    assert bot._kb.asked == ["就一个问题"]
