# -*- coding: utf-8 -*-
"""回复多样性：近期已发话术注入 prompt 禁止换皮重复 + 相似度拦截。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import personas
from wxbot.bot import Bot
from wxbot.config import BotConfig

DUP = "哈哈被十二画说中了，物业确实不在群里，是我疏忽了 [捂脸]"
DUP2 = "哈哈被十二画说中了，物业确实不在群里，是我疏忽了 😊"
DIFF = "这个得找施工方报备，物业只能催，我明天帮你问问联系人"


# ---------- 相似度判定 ----------

def test_too_similar_identical_and_variant():
    assert personas.too_similar(DUP, [DUP])
    assert personas.too_similar(DUP, [DUP2])       # 只换表情的换皮重复


def test_too_similar_different_topics():
    assert not personas.too_similar(DUP, [DIFF])
    assert not personas.too_similar(DUP, [])       # 无历史不拦


def test_too_similar_short_texts_exempt():
    assert not personas.too_similar("哈哈", ["哈哈", "哈哈"])


# ---------- prompt 注入近期已发话术 ----------

class CaptureLLM:
    def __init__(self, reply="好的"):
        self.reply = reply
        self.system = None
        self.user = None

    def chat(self, system, user, temperature=0.8):
        self.system, self.user = system, user
        return self.reply


class FakeCfg:
    llm_cfg = {}

    def persona(self, name):
        return {"style": "热心靠谱", "background": "技术群管家"}


def _engine():
    return personas.PersonaEngine(FakeCfg())


def test_context_reply_injects_avoid_lines():
    e = _engine()
    llm = CaptureLLM()
    e._llm = llm
    e.context_reply("群管家-热心", "测试群", ["甲: 你好"], "ref",
                    avoid_lines=[DUP])
    assert "禁止重复" in llm.user and DUP in llm.user


def test_context_reply_no_avoid_no_marker():
    e = _engine()
    llm = CaptureLLM()
    e._llm = llm
    e.context_reply("群管家-热心", "测试群", ["甲: 你好"])
    assert "禁止重复" not in llm.user


def test_group_answer_injects_avoid_lines():
    e = _engine()
    llm = CaptureLLM()
    e._llm = llm
    e.group_answer("通用朋友", "测试群", ["甲: q"], "q", "甲",
                   avoid_lines=[DUP])
    assert "禁止重复" in llm.user and DUP in llm.user


def test_casual_group_reply_injects_avoid_lines():
    e = _engine()
    llm = CaptureLLM()
    e._llm = llm
    e.casual_group_reply("通用朋友", "测试群", ["甲: 哈哈"], "甲", "哈哈",
                         avoid_lines=[DUP])
    assert "禁止重复" in llm.user and DUP in llm.user


# ---------- bot 侧：近史读取与重复拦截 ----------

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
    no_at_reply: true
""" % db


class FakeDB:
    def get_messages(self, gid, limit=500, offset=0):
        return []


class FakeDriver:
    def __init__(self):
        self.self_id = "wxid_bot"
        self.self_name = "伟"
        self.db = FakeDB()

    def baseline(self, user, lookback=50):
        return 0

    def mention_names(self, group_cfg):
        return ["伟"]

    def display_of(self, uid):
        return {"wxid_a": "阿强"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return ["阿强: 物业不在群里吗"]


class ScriptedLLM:
    """context_reply 按脚本吐字，记录每次入参。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.users = []

    def chat(self, system, user, temperature=0.8):
        self.users.append(user)
        return self.replies.pop(0) if self.replies else "换了个说法呢"

    def chat_json(self, system, user, temperature=0.1):
        return {"reply": True, "reason": "有人提问"}


def _make_bot(tmp_path, llm):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())
    b.personas._llm = llm
    b.build_watch()
    return b


def test_recent_replies_reads_own_logs(tmp_path):
    b = _make_bot(tmp_path, ScriptedLLM(["x"]))
    b.send("测试群", DUP, "no_at")
    b.send("别的群", "另一条", "no_at")
    rec = b._recent_replies("测试群")
    assert rec == [DUP]                            # 只读本会话、只读机器人发出类


def test_no_at_duplicate_blocked_then_retry(tmp_path):
    """第一次与近期回复换皮重复 → 带更强 avoid 重试 → 新话术发出。"""
    b = _make_bot(tmp_path, ScriptedLLM([DUP2, DIFF]))
    b.send("测试群", DUP, "no_at")                 # 造一条近期回复
    b.state["no_at_last"]["111@chatroom"] = 0
    b._no_at_reply(b.watch[0], {"type": "文本", "is_self": False,
                                "sender": "wxid_a", "sender_display": "阿强",
                                "content": "物业不在群里吗", "seq": 11, "ts": 1})
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "no_at"]
    assert len(recs) == 2                          # DUP + DIFF 都发出
    assert DIFF in recs[0]["detail"]
    assert "禁止重复" in b.personas._llm.users[-1]  # 重试时注入了更长的 avoid


def test_no_at_always_duplicate_stays_silent(tmp_path):
    """两次都换皮重复 → 宁可沉默，不刷屏。"""
    b = _make_bot(tmp_path, ScriptedLLM([DUP2, DUP2 + "！"]))
    b.send("测试群", DUP, "no_at")
    before = len(b.store.recent_logs(50))
    b.state["no_at_last"]["111@chatroom"] = 0
    b._no_at_reply(b.watch[0], {"type": "文本", "is_self": False,
                                "sender": "wxid_a", "sender_display": "阿强",
                                "content": "物业不在群里吗", "seq": 11, "ts": 1})
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "no_at"]
    assert len(recs) == 1                          # 只有最初那条
    assert b.state["no_at_last"].get("111@chatroom", 0) == 0   # 沉默不占冷却
