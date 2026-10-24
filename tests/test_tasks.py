# -*- coding: utf-8 -*-
"""自定义定时任务：群主/管理员 @机器人固化指令（每日总结等）+ 定时调度执行。"""
import datetime as _dt
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.bot import Bot
from wxbot.config import BotConfig
from wxbot.store import Store


# ---------- store：group_tasks 表与到期判定 ----------

def test_task_crud_and_due(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    tid = s.add_task("111@chatroom", "测试群", hour=21, minute=0,
                     instruction="总结今天群里的消息", created_by="wxid_owner",
                     created_by_name="群主")
    tasks = s.list_tasks("111@chatroom")
    assert len(tasks) == 1 and tasks[0]["id"] == tid
    assert tasks[0]["hour"] == 21 and tasks[0]["freq"] == "daily"

    now = _dt.datetime(2026, 10, 23, 21, 30).timestamp()
    with s._conn() as c:
        c.execute("UPDATE group_tasks SET last_run=0, created_ts=1")
    due = s.due_tasks(now)
    assert [t["id"] for t in due] == [tid]
    due = s.due_tasks(now)
    assert [t["id"] for t in due] == [tid]
    s.mark_run(tid, ts=now)
    assert s.due_tasks(now) == []               # 跑过不再触发

    s.remove_task(tid)
    assert s.list_tasks("111@chatroom") == []


def test_task_due_not_before_slot(tmp_path):
    """21:00 的任务在当天 15:00 不到期（即使从未跑过）。"""
    s = Store(str(tmp_path / "t.db"))
    s.add_task("111@chatroom", "测试群", hour=21, minute=0,
               instruction="x", created_by="o", created_by_name="o")
    with s._conn() as c:
        c.execute("UPDATE group_tasks SET last_run=0")
    now = _dt.datetime(2026, 10, 23, 15, 0).timestamp()
    assert s.due_tasks(now) == []


# ---------- personas：指令解析 + 每日总结生成 ----------

class FakeCfg:
    llm_cfg = {}

    def persona(self, name):
        return {"style": "热心靠谱", "background": "技术群管家"}


def test_parse_task_command():
    from wxbot import personas
    e = personas.PersonaEngine(FakeCfg())
    seen = {}

    class FL:
        def chat_json(self, system, user, temperature=0.1):
            seen["system"], seen["user"] = system, user
            return {"is_task": True, "action": "create", "freq": "daily",
                    "hour": 21, "minute": 0, "desc": "群消息总结"}

    e._llm = FL()
    d = e.parse_task_command("每天晚上9点总结一下群里消息")
    assert d["action"] == "create" and d["hour"] == 21
    assert "任务解析器" in seen["system"]


def test_summarize_day():
    from wxbot import personas
    e = personas.PersonaEngine(FakeCfg())
    seen = {}

    class FL:
        def chat(self, system, user, temperature=0.8):
            seen["system"], seen["user"] = system, user
            return "今日要点：物业通知明早停水；有人问地铁12号线；张三求推荐装修师傅"

    e._llm = FL()
    text = e.summarize_day("测试群", ["张三: 明早停水吗", "李四: 是的7点到11点"],
                           "总结今天群里的消息")
    assert "今日要点" in text
    assert "测试群" in seen["user"] and "停水" in seen["user"]


# ---------- bot：@指令识别、权限、创建/列表/取消、到期执行 ----------

def _cfg_text(tmp_path):
    db = str(tmp_path / "t.db").replace("\\", "/")
    return """
app: {dry_run: true, db_file: "%s"}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
admins: ["wxid_admin"]
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    qa: {enabled: true, grounded_reply: true}
""" % db


class FakeDB:
    def __init__(self):
        self.rows = [
            {"sort_seq": 1, "create_time": time.time() - 60, "sender_username": "wxid_a",
             "type": "文本", "local_id": 1, "content": "wxid_a:\n明早停水吗"},
            {"sort_seq": 2, "create_time": time.time() - 30, "sender_username": "wxid_b",
             "type": "文本", "local_id": 2, "content": "wxid_b:\n是的7点到11点"},
        ]

    def get_messages(self, gid, limit=500, offset=0):
        return list(self.rows)[:limit]

    def get_room_owner(self, gid):
        return "wxid_owner"


class FakeDriver:
    def __init__(self):
        self.self_id = "wxid_bot"
        self.self_name = "伟"
        self.db = FakeDB()

    def baseline(self, user, lookback=50):
        return 0

    def chat_owner(self, gid):
        return self.db.get_room_owner(gid)

    def mention_names(self, group_cfg):
        return ["伟"]

    def display_of(self, uid):
        return {"wxid_a": "阿强", "wxid_admin": "管理员", "wxid_owner": "群主",
                "wxid_bot": "智能机器人"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return []

    def send_text(self, display, text):
        return True, "ok"


class RoutingLLM:
    """按 system 关键词路由：任务解析 / 沉淀 / 提问理解 / 总结生成。"""

    def __init__(self, parse=None, summary="今日要点：停水通知+地铁讨论"):
        self.parse = parse or {"is_task": True, "action": "create", "freq": "daily",
                               "hour": 21, "minute": 0, "desc": "群消息总结"}
        self.summary = summary
        self.users = []

    def chat_json(self, system, user, temperature=0.1):
        self.users.append(user)
        if "任务解析器" in system:
            return dict(self.parse)
        if "items" in system:
            return {"items": []}
        return {"standalone": "", "should_answer": True}

    def chat(self, system, user, temperature=0.8):
        self.users.append(user)
        return self.summary


def _make_bot(tmp_path, llm):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())
    b.personas._llm = llm
    b.build_watch()
    return b


def _msg(content, sender="wxid_admin", name="管理员"):
    return {"type": "文本", "is_self": False, "sender": sender,
            "sender_display": name, "seq": 10, "ts": 1,
            "content": content, "at_users": ["wxid_bot"]}


def test_admin_create_task(tmp_path):
    b = _make_bot(tmp_path, RoutingLLM())
    assert b._maybe_task_command(b.watch[0], _msg("@伟 每天晚上9点总结一下群里消息"))
    tasks = b.store.list_tasks("111@chatroom")
    assert len(tasks) == 1 and tasks[0]["hour"] == 21
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]
    assert recs and "21:00" in recs[0]["detail"]      # 确认话术带时间


def test_owner_create_task(tmp_path):
    b = _make_bot(tmp_path, RoutingLLM())
    assert b._maybe_task_command(
        b.watch[0], _msg("@伟 每天早上8点提醒大家打卡", sender="wxid_owner", name="群主"))
    assert len(b.store.list_tasks("111@chatroom")) == 1


def test_normal_member_cannot_create(tmp_path):
    b = _make_bot(tmp_path, RoutingLLM())
    assert not b._maybe_task_command(
        b.watch[0], _msg("@伟 每天晚上9点总结群里消息", sender="wxid_a", name="阿强"))
    assert b.store.list_tasks("111@chatroom") == []


# ---------- 非管理员的任务式请求：指路而非硬答 ----------

def test_nonadmin_task_request_deflected(tmp_path):
    b = _make_bot(tmp_path, RoutingLLM())
    m = _msg("@伟 每天晚上9点回顾总结群里发生的事情", sender="wxid_a", name="阿强")
    assert b._deflect_nonadmin_task(b.watch[0], m, "每天晚上9点回顾总结群里发生的事情")
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]
    assert recs and "管理员" in recs[0]["detail"]      # 指路话术


def test_admin_task_request_not_deflected(tmp_path):
    b = _make_bot(tmp_path, RoutingLLM())
    m = _msg("@伟 每天晚上9点总结群里消息")            # wxid_admin 是管理员
    assert not b._deflect_nonadmin_task(b.watch[0], m, "每天晚上9点总结群里消息")
    assert not [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]


def test_daily_life_question_not_deflected(tmp_path):
    b = _make_bot(tmp_path, RoutingLLM())
    m = _msg("@伟 每天锻炼对身体好吗", sender="wxid_a", name="阿强")
    assert not b._deflect_nonadmin_task(b.watch[0], m, "每天锻炼对身体好吗")
    m2 = _msg("@伟 总结一下NETLIST怎么配", sender="wxid_a", name="阿强")
    assert not b._deflect_nonadmin_task(b.watch[0], m2, "总结一下NETLIST怎么配")
    assert not [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]


def test_irrelevant_question_not_parsed_as_task(tmp_path):
    llm = RoutingLLM(parse={"is_task": False})
    b = _make_bot(tmp_path, llm)
    assert not b._maybe_task_command(b.watch[0], _msg("@伟 NETLIST_FILE 怎么配"))
    assert b.store.list_tasks("111@chatroom") == []


# ---------- "每日统计"一族：定时词/任务动词没识别导致答非所问（真实翻车） ----------

def test_daily_stats_recognized_for_admin(tmp_path):
    """管理员说"每日统计"：关键词闸门放行 → 建任务并答复（不再掉进知识库）。"""
    llm = RoutingLLM(parse={"is_task": True, "action": "create", "freq": "daily",
                            "hour": 20, "minute": 0, "desc": "每日群统计"})
    b = _make_bot(tmp_path, llm)
    assert b._maybe_task_command(b.watch[0], _msg("@伟 每日统计一下群里的活跃情况"))
    tasks = b.store.list_tasks("111@chatroom")
    assert len(tasks) == 1 and tasks[0]["hour"] == 20


def test_daily_stats_nonadmin_deflected(tmp_path):
    """非管理员说"每日统计"：明说没权限、指路群主/管理员，不硬答知识库。"""
    llm = RoutingLLM()
    b = _make_bot(tmp_path, llm)
    m = _msg("@伟 每日统计一下群里的活跃情况", sender="wxid_a", name="阿强")
    assert b._deflect_nonadmin_task(b.watch[0], m, "每日统计一下群里的活跃情况")
    assert b.store.list_tasks("111@chatroom") == []
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]
    assert recs and "管理员" in recs[0]["detail"]


def test_list_and_cancel(tmp_path):
    llm = RoutingLLM()
    b = _make_bot(tmp_path, llm)
    b._maybe_task_command(b.watch[0], _msg("@伟 每天晚上9点总结群里消息"))
    llm.parse = {"is_task": True, "action": "list"}
    assert b._maybe_task_command(b.watch[0], _msg("@伟 看看现在的定时任务"))
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]
    assert any("1." in r["detail"] for r in recs)
    llm.parse = {"is_task": True, "action": "cancel", "index": 1}
    assert b._maybe_task_command(b.watch[0], _msg("@伟 取消第一个任务"))
    assert b.store.list_tasks("111@chatroom") == []


def test_due_task_executes_and_posts_summary(tmp_path):
    b = _make_bot(tmp_path, RoutingLLM())
    tid = b.store.add_task("111@chatroom", "测试群", hour=0, minute=0,
                           instruction="总结今天群里的消息", created_by="wxid_owner",
                           created_by_name="群主")
    with b.store._conn() as c:
        c.execute("UPDATE group_tasks SET last_run=0, created_ts=1")   # 造一个今天已到期的
    b.state["last_task_check"] = 0
    b._maybe_run_tasks()
    deadline = time.time() + 5
    while time.time() < deadline:
        if any(r["kind"] == "reply" and "今日要点" in r["detail"]
               for r in b.store.recent_logs(50)):
            break
        time.sleep(0.05)
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "reply"]
    assert recs and "今日要点" in recs[0]["detail"]
    assert b.store.list_tasks("111@chatroom")[0]["id"] == tid
    assert b.store.list_tasks("111@chatroom")[0]["last_run"] > 0
