# -*- coding: utf-8 -*-
"""R2 主人对话台：文件传输助手 is_self 消息 = 主人对助手说话。"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import recall as recall_mod
from wxbot.bot import Bot
from wxbot.config import BotConfig

FH = "文件传输助手"


def _cfg_text(tmp_path, extra_app=""):
    db = str(tmp_path / "t.db").replace("\\", "/")
    return """
app: {dry_run: true, db_file: "%s"%s}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
admins: ["wxid_admin"]
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    qa: {enabled: true}
""" % (db, extra_app)


class FakeDB:
    def get_messages(self, gid, limit=500, offset=0):
        return []

    def get_room_owner(self, gid):
        return "wxid_owner"

    def get_sessions(self, limit=60):
        return []


class FakeDriver:
    def __init__(self):
        self.self_id = "wxid_bot"
        self.self_name = "智能机器人"
        self.db = FakeDB()

    def baseline(self, user, lookback=50):
        return 0

    def list_recent_chats(self, limit=60):
        return []

    def chat_owner(self, gid):
        return "wxid_owner"

    def mention_names(self, group_cfg):
        return ["智能机器人"]

    def display_of(self, uid):
        return {"wxid_admin": "管理员", "wxid_bot": "智能机器人"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return []

    def send_text(self, display, text):
        return True, "ok"


def _mk_bot(tmp_path, extra_app=""):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path, extra_app), encoding="utf-8")
    return Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())


def _owner_msg(content, ts=None):
    return {"type": "文本", "is_self": True, "sender": "wxid_bot",
            "sender_display": "智能机器人", "seq": 10,
            "ts": ts or time.time(), "content": content}


# ---------- 对话台挂载 ----------

def test_console_watch_built_by_default(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    consoles = [w for w in b.watch if w["kind"] == "console"]
    assert len(consoles) == 1
    assert consoles[0]["display"] == FH


def test_console_watch_disabled(tmp_path):
    b = _mk_bot(tmp_path, extra_app=', console: {enabled: false}')
    b.build_watch()
    assert not [w for w in b.watch if w["kind"] == "console"]


# ---------- 主人消息处理 ----------

def test_owner_question_answered(tmp_path, monkeypatch):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "console"][0]
    asked = []

    def _fake_answer(bot, q):
        asked.append(q)
        return "阿强前天在【测试群】问过停水"

    monkeypatch.setattr(recall_mod, "answer", _fake_answer)
    b._on_console_msgs(w, [_owner_msg("群里谁问过停水")])
    assert asked == ["群里谁问过停水"]
    recs = [r for r in b.store.recent_logs(20) if r["kind"] == "recall"]
    assert recs and "停水" in recs[0]["detail"]


def test_bot_own_reply_not_looped(tmp_path, monkeypatch):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "console"][0]
    b._last_sent_cache[FH] = ("日报正文：今日热点…", time.time())
    called = []
    monkeypatch.setattr(recall_mod, "answer",
                        lambda bot, q: called.append(q) or "x")
    b._on_console_msgs(w, [_owner_msg("日报正文：今日热点…")])
    assert called == []                       # 自己刚发的内容不当成主人提问


def test_hash_command_passthrough(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "console"][0]
    b._on_console_msgs(w, [_owner_msg("#补报")])
    recs = [r for r in b.store.recent_logs(20) if r["kind"] == "admin"]
    assert recs and "漏看消息" in recs[0]["detail"]


def test_nontext_and_nonself_ignored(tmp_path, monkeypatch):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "console"][0]
    called = []
    monkeypatch.setattr(recall_mod, "answer",
                        lambda bot, q: called.append(q) or "x")
    b._on_console_msgs(w, [
        {"type": "图片", "is_self": True, "sender": "wxid_bot",
         "sender_display": "", "seq": 11, "ts": time.time(), "content": "[图片]"},
        {"type": "文本", "is_self": False, "sender": "wxid_bot",
         "sender_display": "", "seq": 12, "ts": time.time(), "content": "别人不可能"},
    ])
    assert called == []


def test_console_works_while_paused(tmp_path, monkeypatch):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "console"][0]
    b.state["paused"] = True
    monkeypatch.setattr(recall_mod, "answer", lambda bot, q: "答")
    b._on_console_msgs(w, [_owner_msg("暂停时也能问")])
    assert [r for r in b.store.recent_logs(20) if r["kind"] == "recall"]
