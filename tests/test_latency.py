# -*- coding: utf-8 -*-
"""响应提速回归：群间不互相阻塞（per-watch worker）、KB 沉淀后台化、
私聊 quiesce 智能跳过、UIA 发送跨线程串行化、KB 应答检索快速失败。"""
import os
import sys
import threading
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
reply: {quiesce_sec: 0.5}
contacts:
  - {id: wxid_friend, display: 老王, enabled: true}
groups:
  - id: "111@chatroom"
    display: 慢群
    enabled: true
    active: true
    no_at_reply: true
    qa: {enabled: true, grounded_reply: true}
  - id: "222@chatroom"
    display: 快群
    enabled: true
    qa: {enabled: true, grounded_reply: true}
""" % db


class FakeDB:
    def __init__(self):
        self.rows = [
            {"sort_seq": 100, "create_time": NOW - 60, "sender_username": "wxid_a",
             "type": "文本", "local_id": 1, "content": "wxid_a:\n水压不够怎么办"},
            {"sort_seq": 101, "create_time": NOW - 30, "sender_username": "wxid_b",
             "type": "文本", "local_id": 2, "content": "wxid_b:\n先查总阀再放放气"},
        ]

    def get_messages(self, gid, limit=500, offset=0):
        return list(self.rows)[:limit]


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
        return {"wxid_a": "阿强", "wxid_b": "李四", "wxid_friend": "老王",
                "wxid_bot": "群助手"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return ["张三: 你好", "李四: 在吗"]

    def poll(self, user, since):
        return [], since

    def send_text(self, display, text):
        return True, "ok"


class SlowChatLLM:
    """chat 可配延迟（模拟慢模型）；chat_json 走任务/理解/沉淀路由。"""

    def __init__(self, chat_delay=0.0, sink_delay=0.0):
        self.chat_delay = chat_delay
        self.sink_delay = sink_delay
        self.sink_runs = []

    def chat_json(self, system, user, temperature=0.1):
        if "知识库管理员" in system:                     # KB 沉淀提取
            self.sink_runs.append(time.time())
            time.sleep(self.sink_delay)
            return {"items": [{"q": "水压不够咋办", "a": "先查总阀"}]}
        if "任务解析器" in system:
            return {"is_task": False}
        return {"standalone": user.split("问题：")[-1][:50], "should_answer": True}

    def chat(self, system, user, temperature=0.8):
        time.sleep(self.chat_delay)
        return "慢群接话：这个我来说两句"


class FakeKB:
    def __init__(self):
        self.delay = 0.0

    def ask(self, question, room=""):
        if self.delay:
            time.sleep(self.delay)
        return ("标准答案：" + question, True, True)

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


def _wait_log(bot, kind, contains, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for r in bot.store.recent_logs(50):
            if r["kind"] == kind and contains in (r["detail"] or ""):
                return True
        time.sleep(0.02)
    return False


# ---------- 慢群不阻塞快群 ----------
def test_slow_group_does_not_block_fast_group(bot):
    """慢群（active 免@链路，模型 3 秒）处理期间，快群 @问题照常秒答。"""
    bot.personas._llm = SlowChatLLM(chat_delay=3.0)
    slow = next(w for w in bot.watch if w["display"] == "慢群")
    fast = next(w for w in bot.watch if w["display"] == "快群")

    t0 = time.time()
    bot._dispatch(slow, [_msg("群友闲聊一句", seq=1)])           # 先压慢的
    bot._dispatch(fast, [_msg("@群助手 水表怎么读", seq=2)])     # 再来快的
    ok = _wait_log(bot, "qa", "水表", timeout=2.5)
    el = time.time() - t0
    assert ok, f"快群应答未在 2.5s 内出现（{el:.1f}s）——慢群阻塞了主循环"


def test_dispatch_same_watch_keeps_fifo(bot):
    """同一会话内保序：先后入队两条，先入队的先落日志。"""
    bot.personas._llm = SlowChatLLM(chat_delay=0.2)
    fast = next(w for w in bot.watch if w["display"] == "快群")
    bot._dispatch(fast, [_msg("@群助手 第一个问题", seq=1)])
    bot._dispatch(fast, [_msg("@群助手 第二个问题", seq=2)])
    assert _wait_log(bot, "qa", "第一个问题", timeout=3)
    assert _wait_log(bot, "qa", "第二个问题", timeout=3)
    recs = [r for r in bot.store.recent_logs(50) if r["kind"] == "qa"]
    i1 = next(i for i, r in enumerate(recs) if "第一个问题" in r["detail"])
    i2 = next(i for i, r in enumerate(recs) if "第二个问题" in r["detail"])
    assert i1 > i2          # recent_logs 倒序：先答的在后面


# ---------- KB 沉淀后台化 ----------
def test_sink_runs_in_background(bot):
    """免@链路里 KB 沉淀（LLM 提炼 0.8s）不再阻塞处理本身。"""
    bot.personas._llm = SlowChatLLM(chat_delay=0.0, sink_delay=0.8)
    slow = next(w for w in bot.watch if w["display"] == "慢群")
    t0 = time.time()
    bot._no_at_reply(slow, _msg("刚聊完一个话题", seq=3))
    el = time.time() - t0
    assert el < 0.4, f"免@处理被沉淀拖了 {el:.1f}s，应后台执行"


def test_sink_async_no_reentry(bot):
    """沉淀进行中重复触发直接跳过，不并发跑两份。"""
    bot.personas._llm = SlowChatLLM(sink_delay=0.8)
    slow = next(w for w in bot.watch if w["display"] == "慢群")
    bot._sink_to_kb_async(slow)
    bot._sink_to_kb_async(slow)          # 第一次还在跑，应被防重入挡掉
    deadline = time.time() + 4
    while time.time() < deadline and len(bot._kb_writer.calls) < 1:
        time.sleep(0.05)
    time.sleep(0.3)                      # 给可能漏网的重入留时间
    assert len(bot._kb_writer.calls) == 1
    assert len(bot.personas._llm.sink_runs) == 1


# ---------- 私聊 quiesce 智能跳过 ----------
def test_quiesce_skips_when_already_idle(bot):
    """对方消息已静默超过 quiesce 秒 → 不再傻等合并窗口。"""
    bot.personas._llm = SlowChatLLM()
    w = next(x for x in bot.watch if x["kind"] == "contact")
    t0 = time.time()
    bot._reply_contact(w, [_msg("在吗", sender="wxid_friend", name="老王",
                                ts=time.time() - 6)])
    assert time.time() - t0 < 0.45


def test_quiesce_waits_when_still_bursting(bot):
    """对方刚发完（可能还在连发）→ 仍等满 quiesce 窗口合并。"""
    bot.personas._llm = SlowChatLLM()
    w = next(x for x in bot.watch if x["kind"] == "contact")
    t0 = time.time()
    bot._reply_contact(w, [_msg("在吗", sender="wxid_friend", name="老王",
                                ts=time.time())])
    assert time.time() - t0 >= 0.5


# ---------- UIA 发送跨线程串行化 ----------
def test_sends_serialized_across_threads():
    """两个线程同时发消息（不同会话）：UIA 层物理互斥，绝不并发操作窗口。"""
    from wxbot.driver import WxDriver
    d = object.__new__(WxDriver)

    state = {"cur": 0, "max": 0, "lk": threading.Lock()}

    class UIA:
        def __init__(self):
            self.opened = ""

        def open_chat(self, display):
            self.opened = display
            return True

        def current_chat(self):
            return self.opened

        def send_text(self, text):
            with state["lk"]:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            time.sleep(0.1)
            with state["lk"]:
                state["cur"] -= 1
            return True

    d.uia = UIA()

    def _one(display):
        assert d.send_text(display, "hi")[0]

    ts = [threading.Thread(target=_one, args=(f"群{i}",)) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert state["max"] == 1, f"UIA 发送出现并发（max={state['max']}）"


# ---------- KB 应答检索快速失败 ----------
def test_kb_ask_uses_short_timeout(monkeypatch):
    """ask/search 走短超时（默认 8s，应答等不了 RAG 慢响应）；add_qa 保持长超时。"""
    import answer
    from wxbot.kb import KBGateway
    captured = {}

    class R:
        status_code = 200
        text = ('{"jsonrpc":"2.0","id":1,"result":{"content":'
                '[{"type":"text","text":"答案内容很长很长很长 [1] 来源：文档"}]}}')

    def fake_post(url, **kw):
        captured.update(kw)
        return R()

    monkeypatch.setattr(answer.requests, "post", fake_post)
    gw = KBGateway({"mcp": {"url": "http://x/mcp", "api_key": "k",
                            "kb": "wxbot", "timeout": 60}})
    gw.ask("问题")
    assert captured["timeout"] == 8
    gw.search("问题")
    assert captured["timeout"] == 8
    gw.add_qa("q", "a")
    assert captured["timeout"] == 60
