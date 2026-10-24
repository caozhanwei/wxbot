# -*- coding: utf-8 -*-
"""@答疑择新：一批多条@只答最新（不再舍近求远答老问题）+ 过气@跳过 + 群级指令管理员按昵称生效。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.bot import Bot
from wxbot.config import BotConfig

NOW = time.time()
TASK_DESC = {"is_task": True, "action": "create", "hour": 21, "minute": 0, "desc": "总结群消息"}


def _cfg_text(tmp_path):
    db = str(tmp_path / "t.db").replace("\\", "/")
    return """
app: {dry_run: true, db_file: "%s"}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
admins: [wxid_root]
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    qa: {enabled: true, grounded_reply: true}
    manage: {welcome: false, forbidden_words: [], warn_persona: 群管家-热心, warn_threshold: 2}
""" % db


class FakeDB:
    def get_messages(self, gid, limit=500, offset=0):
        return []


class FakeDriver:
    def __init__(self):
        self.self_id = "wxid_bot"
        self.self_name = "群助手"
        self.db = FakeDB()

    def baseline(self, user, lookback=50):
        return 0

    def mention_names(self, group_cfg):
        return ["群助手"]

    def display_of(self, uid):
        return {"wxid_a": "阿强", "wxid_b": "老白-业主", "wxid_bot": "群助手"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return []

    def send_text(self, display, text):
        return True, "ok"


class FakeLLM:
    def __init__(self, task=None):
        self.task = task
        self.chatted = 0

    def chat_json(self, system, user, temperature=0.1):
        if "任务解析器" in system:
            return dict(self.task) if self.task else {"is_task": False}
        return {"standalone": "", "should_answer": True}

    def chat(self, system, user, temperature=0.8):
        self.chatted += 1
        return "这是答案"


class FakeKB:
    def __init__(self):
        self.asked = []

    def ask(self, question, room=""):
        self.asked.append(question)
        return ("答案：" + question, True, True)

    def search(self, query, top_k=5):
        return ""


class FakeWriter:
    def __init__(self):
        self.calls = []

    def add_qa(self, question, answer, context=""):
        self.calls.append((question, answer, context))
        return "ok"


@pytest.fixture
def bot(tmp_path):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())
    b._kb = FakeKB()
    b._kb_writer = FakeWriter()
    b.build_watch()
    return b


def _msg(content, sender="wxid_a", name="阿强", seq=10, ts=None):
    return {"type": "文本", "is_self": False, "sender": sender,
            "sender_display": name, "content": content, "seq": seq,
            "ts": NOW if ts is None else ts, "at_users": []}


def _qa_logs(bot):
    return [r for r in bot.store.recent_logs(50) if r["kind"] == "qa"]


# ---------- 只答最新 ----------
def test_answers_only_latest_at_in_batch(bot):
    bot.personas._llm = FakeLLM()
    bot._on_group_msgs(bot.watch[0], [
        _msg("@群助手 老问题一", seq=1, ts=NOW - 60),
        _msg("群友闲聊一句", sender="wxid_b", name="老白-业主", seq=2, ts=NOW - 30),
        _msg("@群助手 新问题二", seq=3, ts=NOW - 5),
    ])
    assert bot._kb.asked == ["新问题二"]
    assert len(_qa_logs(bot)) == 1


def test_single_fresh_at_still_answered(bot):
    bot.personas._llm = FakeLLM()
    bot._on_group_msgs(bot.watch[0], [_msg("@群助手 NETLIST_FILE 怎么配")])
    assert bot._kb.asked == ["NETLIST_FILE 怎么配"]


def test_stale_at_skipped(bot):
    bot.personas._llm = FakeLLM()
    bot._on_group_msgs(bot.watch[0], [_msg("@群助手 几小时前的旧问题", seq=1, ts=NOW - 3600)])
    assert bot._kb.asked == [] and _qa_logs(bot) == []


def test_batch_mixed_stale_and_fresh_answers_fresh(bot):
    bot.personas._llm = FakeLLM()
    bot._on_group_msgs(bot.watch[0], [
        _msg("@群助手 早已过气的老问题", seq=1, ts=NOW - 7200),
        _msg("@群助手 刚刚的新问题", seq=2, ts=NOW - 3),
    ])
    assert bot._kb.asked == ["刚刚的新问题"]


# ---------- 群级指令管理员（昵称即可） ----------
def test_group_admin_by_nickname_can_command(bot):
    bot.personas._llm = FakeLLM(task=TASK_DESC)
    bot.watch[0]["cfg"]["admins"] = ["老白"]        # 群名片是"老白-业主"，按包含匹配
    bot._on_group_msgs(bot.watch[0], [
        _msg("@群助手 每天晚上9点总结一下群里的消息",
             sender="wxid_b", name="老白-业主")])
    tasks = bot.store.list_tasks("111@chatroom")
    assert len(tasks) == 1 and tasks[0]["hour"] == 21 and not bot._kb.asked


def test_non_admin_cannot_command(bot):
    bot.personas._llm = FakeLLM(task=TASK_DESC)
    bot.watch[0]["cfg"]["admins"] = ["老白"]
    bot._on_group_msgs(bot.watch[0], [
        _msg("@群助手 每天晚上9点总结一下群里的消息",
             sender="wxid_a", name="阿强")])
    assert bot.store.list_tasks("111@chatroom") == []
    # 任务式请求不再硬答知识库（曾答非所问被吐槽），改为指路群主/管理员
    recs = [r for r in bot.store.recent_logs(50) if r["kind"] == "qa"]
    assert recs and "管理员" in recs[0]["detail"]
    assert not bot._kb.asked


def test_exact_nickname_admin_matches(bot):
    bot.personas._llm = FakeLLM(task=TASK_DESC)
    bot.watch[0]["cfg"]["admins"] = ["阿强"]
    bot._on_group_msgs(bot.watch[0], [
        _msg("@群助手 每天早上8点提醒大家打卡")])
    assert len(bot.store.list_tasks("111@chatroom")) == 1
