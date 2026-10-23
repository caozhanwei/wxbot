# -*- coding: utf-8 -*-
"""R1 bot 接线：被动私聊监控 / @未答补报 / #补报与主动急停命令。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.bot import Bot
from wxbot.config import BotConfig


def _cfg_text(tmp_path, extra_app=""):
    db = str(tmp_path / "t.db").replace("\\", "/")
    return """
app: {dry_run: true, db_file: "%s"%s}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
admins: ["wxid_admin"]
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
contacts:
  - id: wxid_c
    display: 王五
    enabled: true
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    qa: {enabled: true, grounded_reply: true}
""" % (db, extra_app)


class FakeDB:
    def get_messages(self, gid, limit=500, offset=0):
        return []

    def get_room_owner(self, gid):
        return "wxid_owner"

    def get_sessions(self, limit=60):
        return []


class FakeDriver:
    def __init__(self, sessions=None):
        self.self_id = "wxid_bot"
        self.self_name = "智能机器人"
        self.db = FakeDB()
        self.sessions = sessions or []

    def baseline(self, user, lookback=50):
        return 0

    def list_recent_chats(self, limit=60):
        return list(self.sessions)[:limit]

    def chat_owner(self, gid):
        return "wxid_owner"

    def mention_names(self, group_cfg):
        return ["智能机器人"]

    def display_of(self, uid):
        return {"wxid_admin": "管理员", "wxid_a": "阿强", "wxid_b": "李四",
                "wxid_bot": "智能机器人"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return []

    def send_text(self, display, text):
        return True, "ok"


def _mk_bot(tmp_path, driver=None, extra_app=""):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path, extra_app), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=driver or FakeDriver())
    return b


SESSIONS = [
    {"id": "111@chatroom", "display": "测试群", "is_group": True},
    {"id": "wxid_c", "display": "王五", "is_group": False},        # 已监控
    {"id": "wxid_bot", "display": "智能机器人", "is_group": False},  # 自己
    {"id": "filehelper", "display": "文件传输助手", "is_group": False},
    {"id": "gh_abc", "display": "某公众号", "is_group": False},      # 官方号
    {"id": "wxid_b", "display": "李四", "is_group": False},         # 应被动监控
]


# ---------- 被动私聊发现与分发 ----------

def test_build_watch_passive_privates(tmp_path):
    b = _mk_bot(tmp_path, FakeDriver(SESSIONS))
    b.build_watch()
    passive = [w for w in b.watch if w["kind"] == "contact_passive"]
    assert [w["user"] for w in passive] == ["wxid_b"]
    assert passive[0]["display"] == "李四"


def test_build_watch_passive_disabled(tmp_path):
    b = _mk_bot(tmp_path, FakeDriver(SESSIONS),
                extra_app=', missed_private: {enabled: false}')
    b.build_watch()
    assert not [w for w in b.watch if w["kind"] == "contact_passive"]


def test_passive_msg_recorded_not_replied(tmp_path):
    b = _mk_bot(tmp_path, FakeDriver(SESSIONS))
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "contact_passive"][0]
    b._on_passive_msgs(w, [{
        "type": "文本", "is_self": False, "sender": "wxid_b",
        "sender_display": "李四", "seq": 5, "ts": time.time(),
        "content": "在吗，问你个事"}])
    rows = b.store.missed_unconsumed(src="private")
    assert len(rows) == 1 and rows[0]["chat"] == "李四"
    # 只记录不回复：不产生任何 reply 日志
    assert not [r for r in b.store.recent_logs(20) if r["kind"] == "reply"]


# ---------- 群内 @未答 → 补报 ----------

def _at(content, sender="wxid_a", name="阿强", ts=None, at=True):
    return {"type": "文本", "is_self": False, "sender": sender,
            "sender_display": name, "seq": 10, "ts": ts or time.time(),
            "content": content,
            "at_users": ["wxid_bot"] if at else []}


def test_superseded_at_recorded(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = b.watch[-1]                      # 测试群
    b._group_qa = lambda gw, m: None     # 假装已作答最新一条
    old = _at("@智能机器人 老问题", ts=time.time() - 600)
    new = _at("@智能机器人 新问题")
    b._on_group_msgs(w, [old, new])
    rows = b.store.missed_unconsumed(src="at")
    assert len(rows) == 1 and "老问题" in rows[0]["content"]


def test_all_stale_at_recorded(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = b.watch[-1]
    stale1 = _at("@智能机器人 昨天的问题", ts=time.time() - 3600)
    stale2 = _at("@智能机器人 更早的问题", ts=time.time() - 7200)
    b._on_group_msgs(w, [stale1, stale2])
    rows = b.store.missed_unconsumed(src="at")
    assert len(rows) == 2                # 全过气：都补报


def test_paused_at_recorded(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = b.watch[-1]
    b.state["paused"] = True
    b._on_group_msgs(w, [_at("@智能机器人 暂停期间的问题")])
    rows = b.store.missed_unconsumed(src="at")
    assert len(rows) == 1
    # 非@消息不补报
    b._on_group_msgs(w, [_at("普通发言", ts=time.time(), at=False)])
    assert len(b.store.missed_unconsumed(src="at")) == 1


# ---------- 管理员命令 ----------

def test_admin_commands(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "contact_passive"]
    assert w == []                       # 无最近会话 → 无被动项
    b.store.add_missed("private", "李四", "wxid_b", "李四", "问方案", time.time())
    out = b._do_admin("补报", [])
    assert "李四" in out and "问方案" in out
    assert b._do_admin("急停主动", []) .startswith("已急停")
    assert b.gate.stopped is True
    ok, why = b.gate.allow("digest", "文件传输助手", "x", urgent=True)
    assert ok is False                   # 急停连急件也拦
    b._do_admin("恢复主动", [])
    assert b.gate.stopped is False
    # 两词形式：#急停 主动
    b._do_admin("急停", ["主动"])
    assert b.gate.stopped is True
    b._do_admin("恢复", ["主动"])
    assert b.gate.stopped is False
    assert "补报" in b._do_admin("帮助", [])
