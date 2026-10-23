# -*- coding: utf-8 -*-
"""R1 感知闭环：store 扩表（missed/digest_log/daily_runs/daily_stats）+ digest 生成。

自包含 fake：FakeLLM/FakeDriver，沿用 tests/test_recent_qa.py 模式。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.store import Store
from wxbot import digest as digest_mod

NOW = time.time()
PUSH = "文件传输助手"


def _mk_store(tmp_path):
    return Store(str(tmp_path / "t.db"))


# ---------- store：missed_cache ----------

def test_missed_roundtrip(tmp_path):
    s = _mk_store(tmp_path)
    s.add_missed("at", "测试群", "wxid_a", "阿强", "@机器人 怎么续费", NOW)
    s.add_missed("private", "李四", "wxid_b", "李四", "在吗，有个急事", NOW, urgency="urgent")
    rows = s.missed_unconsumed()
    assert len(rows) == 2
    assert rows[0]["src"] == "at" and rows[1]["urgency"] == "urgent"
    s.mark_missed_consumed([r["id"] for r in rows])
    assert s.missed_unconsumed() == []


def test_missed_scope_filter(tmp_path):
    s = _mk_store(tmp_path)
    s.add_missed("at", "群甲", "wxid_a", "阿强", "问题一", NOW)
    s.add_missed("private", "李四", "wxid_b", "李四", "问题二", NOW)
    only_at = s.missed_unconsumed(src="at")
    assert len(only_at) == 1 and only_at[0]["chat"] == "群甲"


# ---------- store：digest_log / daily_runs / daily_stats ----------

def test_digest_watermark(tmp_path):
    s = _mk_store(tmp_path)
    assert not s.digest_done_day("群甲", "2026-10-23")
    s.digest_done("群甲", "2026-10-23", last_seq=99)
    assert s.digest_done_day("群甲", "2026-10-23")
    assert not s.digest_done_day("群甲", "2026-10-24")   # 次日重跑
    s.digest_done("群甲", "2026-10-23", last_seq=120)    # 幂等覆盖
    assert s.digest_done_day("群甲", "2026-10-23")


def test_daily_runs_idempotent(tmp_path):
    s = _mk_store(tmp_path)
    assert not s.daily_ran("digest", "2026-10-23")
    s.mark_daily_run("digest", "2026-10-23")
    s.mark_daily_run("digest", "2026-10-23")             # 重复标记不炸
    assert s.daily_ran("digest", "2026-10-23")
    assert not s.daily_ran("digest", "2026-10-24")


def test_daily_stats_upsert(tmp_path):
    s = _mk_store(tmp_path)
    s.upsert_daily_stats("2026-10-23", "群甲", msg_count=120, active_members=8,
                         top_words="停水 物业 维修", pending_count=3)
    s.upsert_daily_stats("2026-10-23", "群甲", msg_count=130, active_members=9,
                         top_words="停水 物业", pending_count=2)   # 覆盖
    rows = s.daily_stats_list("2026-10-23")
    assert len(rows) == 1
    assert rows[0]["msg_count"] == 130 and rows[0]["pending_count"] == 2


# ---------- digest：紧急规则与即时推送 ----------

class _FakeLLM:
    def __init__(self):
        self.summarized = []

    def summarize_day(self, group, lines, instruction):
        self.summarized.append((group, list(lines), instruction))
        return f"[{group} 今日热点] 停水通知与物业费讨论"


class _FakeDB:
    def get_messages(self, gid, limit=500, offset=0):
        return [{"sort_seq": i + 1, "create_time": NOW - i * 60,
                 "sender_username": "wxid_a", "sender_nickname": "阿强",
                 "type": "文本", "content": f"wxid_a:\n讨论停水问题第{i}条",
                 "local_id": i, "source": ""}
                for i in range(30)]


class _FakeDriver:
    def __init__(self):
        self.self_id = "wxid_bot"
        self.self_name = "群助手"
        self.db = _FakeDB()

    def _friendly(self, row, user_id=None):
        return f"{row.get('sender_nickname') or row.get('sender_username')}: {row.get('content', '').split(chr(10))[-1]}"

    def display_of(self, uid):
        return "阿强"


class _FakeBot:
    """dry_run 假 Bot：发送落 store 日志，可断言推送产物。"""

    def __init__(self, tmp_path, groups=None, digest_cfg=None):
        self.store = _mk_store(tmp_path)
        self.driver = _FakeDriver()
        self.personas = _FakeLLM()
        self.cfg = type("C", (), {
            "dry_run": True,
            "data": {
                "app": {"daily_digest": digest_cfg if digest_cfg is not None
                        else {"enabled": True, "hour": 21, "push_to": PUSH}},
            },
            "admins": ["wxid_root"],
        })()
        self.watch = groups if groups is not None else [
            {"user": "111@chatroom", "display": "群甲", "kind": "group",
             "cfg": {"id": "111@chatroom", "display": "群甲", "enabled": True,
                     "digest": {"enabled": True}}},
        ]

    def send(self, display, text, kind="reply"):
        self.store.log(kind, display, "", str(text))
        return True

    def notify_admins(self, text):
        self.store.log("notify", "admins", "", str(text))


def _norm_lines(bot, gid):
    from wxbot import mining
    rows = sorted(bot.driver.db.get_messages(gid), key=lambda r: r["sort_seq"])
    return [x["line"] for x in mining._normalize(bot.driver, rows)]


def test_urgent_rule(tmp_path):
    assert digest_mod.is_urgent("这个事很急，今天必须给答复")
    assert digest_mod.is_urgent("尽快回我一下")
    assert not digest_mod.is_urgent("明天有空聚聚")


def test_run_digest_push_and_watermark(tmp_path):
    bot = _FakeBot(tmp_path)
    bot.store.add_missed("at", "群甲", "wxid_a", "阿强", "@机器人 怎么续费", NOW)
    digest_mod.run_digest(bot)
    # 推送到文件传输助手
    pushes = [r for r in bot.store.recent_logs(50) if r["kind"] == "digest"]
    assert pushes and any("群甲" in (r["detail"] or "") for r in pushes)
    assert any("今日热点" in (r["detail"] or "") for r in pushes)
    # 找你的事一节 + missed 被消化
    assert any("找你的事" in (r["detail"] or "") and "怎么续费" in (r["detail"] or "")
               for r in pushes)
    assert bot.store.missed_unconsumed() == []
    # 水位与统计
    day = digest_mod._today()
    assert bot.store.digest_done_day("111@chatroom", day)
    stats = bot.store.daily_stats_list(day)
    assert stats and stats[0]["grp"] == "111@chatroom"
    assert stats[0]["msg_count"] > 0


def test_run_digest_llm_failure_falls_back(tmp_path):
    bot = _FakeBot(tmp_path)

    def _boom(group, lines, instruction):
        raise RuntimeError("LLM 挂了")

    bot.personas.summarize_day = _boom
    digest_mod.run_digest(bot)
    pushes = [r for r in bot.store.recent_logs(50) if r["kind"] == "digest"]
    assert any("[简版]" in (r["detail"] or "") for r in pushes)


def test_run_digest_respects_disabled_group(tmp_path):
    bot = _FakeBot(tmp_path, groups=[
        {"user": "111@chatroom", "display": "群甲", "kind": "group",
         "cfg": {"enabled": True, "digest": {"enabled": False}}},
    ])
    digest_mod.run_digest(bot)
    assert not [r for r in bot.store.recent_logs(50) if r["kind"] == "digest"]


def test_report_missed_now(tmp_path):
    bot = _FakeBot(tmp_path)
    bot.store.add_missed("private", "李四", "wxid_b", "李四", "问方案定了没", NOW)
    bot.store.add_missed("at", "群甲", "wxid_a", "阿强", "@机器人 怎么续费", NOW)
    text = digest_mod.report_missed_now(bot)
    assert "李四" in text and "怎么续费" in text
    # 即时报不清空缓存（等日报统一消化）
    assert len(bot.store.missed_unconsumed()) == 2


def test_on_private_msg_records_and_urgent_pushes(tmp_path):
    bot = _FakeBot(tmp_path)
    w = {"user": "wxid_b", "display": "李四", "kind": "contact_passive", "cfg": {}}
    digest_mod.on_private_msg(bot, w, {
        "sender": "wxid_b", "sender_display": "李四", "is_self": False,
        "type": "文本", "content": "在吗？装修的事很急", "ts": NOW})
    rows = bot.store.missed_unconsumed(src="private")
    assert len(rows) == 1 and rows[0]["urgency"] == "urgent"
    # 急件即时推送（过 urgent 闸门）
    pushes = [r for r in bot.store.recent_logs(50)
              if r["kind"] == "digest" and "李四" in (r["detail"] or "")]
    assert pushes
    # 普通消息只入库不即时推
    digest_mod.on_private_msg(bot, w, {
        "sender": "wxid_b", "sender_display": "李四", "is_self": False,
        "type": "文本", "content": "周末聚聚", "ts": NOW})
    rows = bot.store.missed_unconsumed(src="private")
    assert len(rows) == 2 and rows[1]["urgency"] == "normal"
