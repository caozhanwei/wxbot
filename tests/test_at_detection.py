# -*- coding: utf-8 -*-
"""@机器人检测：msgsource atuserlist 直读 wxid（群名片≠微信昵称时也能命中）+ 通用 @ 前缀剥离。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import group_logic

U = " "   # 微信 @ 后的四分之一空格分隔符
SRC_ONE = ("<msgsource><atuserlist>"
           "<![CDATA[wxid_yfug457qmr5j22]]></atuserlist></msgsource>")
SRC_TWO = ("<msgsource><atuserlist>"
           "<![CDATA[wxid_a,wxid_b]]></atuserlist></msgsource>")


# ---------- parse_atuserlist ----------

def test_parse_atuserlist_single():
    assert group_logic.parse_atuserlist(SRC_ONE) == ["wxid_yfug457qmr5j22"]


def test_parse_atuserlist_multi():
    assert group_logic.parse_atuserlist(SRC_TWO) == ["wxid_a", "wxid_b"]


def test_parse_atuserlist_empty_and_missing():
    assert group_logic.parse_atuserlist(None) == []
    assert group_logic.parse_atuserlist("") == []
    assert group_logic.parse_atuserlist("<msgsource></msgsource>") == []
    assert group_logic.parse_atuserlist(
        "<atuserlist><![CDATA[]]></atuserlist>") == []


# ---------- strip_generic_at ----------

def test_strip_generic_at_u2005():
    assert group_logic.strip_generic_at(
        "@智能机器人" + U + "你先改了吧") == "你先改了吧"


def test_strip_generic_at_space():
    assert group_logic.strip_generic_at(
        "@智能机器人 请在10秒以内回答出师表第208个字") == "请在10秒以内回答出师表第208个字"


def test_strip_generic_at_no_mention_untouched():
    assert group_logic.strip_generic_at("普通消息不动") == "普通消息不动"


# ---------- driver.poll 透传 ----------

class StubDB:
    def __init__(self, rows):
        self.rows = rows

    def get_new_messages(self, user, since=0, limit=200):
        return list(self.rows)


def _stub_driver(rows):
    from wxbot.driver import WeChatDriver

    class StubDriver(WeChatDriver):
        def __init__(self):
            self.self_id = "wxid_bot"
            self.self_name = "伟"
            self.db = StubDB(rows)
            self.ocr = None
            self.asr = None
            self._ocr_cache = {}
            self._voice_cache = {}
            self._media = None

        def display_of(self, uid):
            return {"wxid_bot": "智能机器人"}.get(uid) or uid

    return StubDriver()


def test_poll_passes_at_users():
    d = _stub_driver([{
        "sort_seq": 9, "type": "文本", "create_time": 1, "local_id": 7,
        "sender_username": "wxid_a",
        "content": "wxid_a:\n@智能机器人" + U + "你先改了吧",
        "source": SRC_ONE,
    }])
    items, since = d.poll("111@chatroom", 0)
    assert since == 9
    assert items[0]["at_users"] == ["wxid_yfug457qmr5j22"]


def test_poll_no_at_users():
    d = _stub_driver([{
        "sort_seq": 9, "type": "文本", "create_time": 1, "local_id": 7,
        "sender_username": "wxid_a", "content": "wxid_a:\n吃饭没",
        "source": "<msgsource></msgsource>",
    }])
    items, _ = d.poll("111@chatroom", 0)
    assert items[0]["at_users"] == []


# ---------- bot 分流端到端（复现线上事故：群名片 @ 不回） ----------

def _cfg_text(tmp_path):
    db = str(tmp_path / "t.db").replace("\\", "/")
    return """
app: {dry_run: true, db_file: "%s"}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    qa: {enabled: true, grounded_reply: true}
""" % db


class FakeLLM:
    def __init__(self, verdict=True, reply="好的没问题",
                 sink_items=None):
        self.verdict = verdict
        self.reply = reply
        self.sink_items = sink_items or []
        self.judged = 0
        self.chatted = 0

    def chat_json(self, system, user, temperature=0.1):
        self.judged += 1
        return {"standalone": "", "should_answer": True}   # 空 → 回落原问题

    def chat(self, system, user, temperature=0.8):
        self.chatted += 1
        return self.reply


class FakeKB:
    def __init__(self):
        self.asked = []

    def ask(self, question, room=""):
        self.asked.append(question)
        return "答案", True, True

    def search(self, query, top_k=5):
        return "参考"


class FakeWriter:
    def __init__(self):
        self.calls = []

    def add_qa(self, question, answer, context=""):
        self.calls.append((question, answer, context))
        return "ok"


class FakeDB:
    def get_messages(self, gid, limit=500, offset=0):
        return []


class CardDriver:
    """微信昵称是'伟'，群里机器人的群名片是'智能机器人'（线上事故现场）。"""

    def __init__(self):
        self.self_id = "wxid_bot"
        self.self_name = "伟"
        self.db = FakeDB()
        self.sent = []

    def baseline(self, user, lookback=50):
        return 0

    def mention_names(self, group_cfg):
        return ["伟"]

    def display_of(self, uid):
        return {"wxid_a": "阿强", "wxid_bot": "智能机器人"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return []

    def send_text(self, display, text):
        self.sent.append((display, text))
        return True, "ok"


def _make_bot(tmp_path):
    from wxbot.bot import Bot
    from wxbot.config import BotConfig
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=CardDriver())
    b._kb = FakeKB()
    b._kb_writer = FakeWriter()
    b.build_watch()
    return b


def _msg(content, at_users=None, seq=10):
    return {"type": "文本", "is_self": False, "sender": "wxid_a",
            "sender_display": "阿强", "seq": seq, "ts": time.time(),
            "content": content, "at_users": at_users or []}


def test_at_by_group_card_routes_to_qa(tmp_path):
    b = _make_bot(tmp_path)
    llm = FakeLLM()
    b.personas._llm = llm
    b._on_group_msgs(b.watch[0], [
        _msg("@智能机器人" + U + "你先改了吧", at_users=["wxid_bot"])])
    assert llm.judged == 1                       # 走了答疑链路
    assert b._kb.asked == ["你先改了吧"]           # @群名片前缀被剥掉


def test_at_by_atuserlist_without_text_name(tmp_path):
    """正文里 @ 名字不匹配任何称呼，仅凭 atuserlist 也要回。"""
    b = _make_bot(tmp_path)
    FakeLLM()
    b.personas._llm = FakeLLM()
    b._on_group_msgs(b.watch[0], [
        _msg("@智能机器人 NETLIST_FILE 怎么配", at_users=["wxid_bot"])])
    assert b._kb.asked == ["NETLIST_FILE 怎么配"]


def test_other_user_at_not_routed(tmp_path):
    b = _make_bot(tmp_path)
    b.personas._llm = FakeLLM()
    b._on_group_msgs(b.watch[0], [
        _msg("@张三 帮我看下这个", at_users=["wxid_zhang"])])
    assert b._kb.asked == []                     # @的是别人，不进答疑
