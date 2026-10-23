# -*- coding: utf-8 -*-
"""KB 问答入库：kb_add_qa 参数、免@沉淀抽取链路、挖掘库批量同步。"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.bot import Bot
from wxbot.config import BotConfig
from wxbot.kb import KBGateway
from wxbot.store import Store
from wxbot import webui

GROUP = "111@chatroom"

YAML_TMPL = """
app: {dry_run: true, db_file: "%s"}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
groups:
  - id: "111@chatroom"
    display: 老司机养成记
    enabled: true
    no_at_reply: true
    qa: {enabled: true, grounded_reply: true}
"""


def _rows():
    return [
        {"sort_seq": 1, "type": "文本", "create_time": 1, "content": "wxid_a: 问 手册在哪"},
        {"sort_seq": 2, "type": "文本", "create_time": 2, "content": "wxid_b: 答 已发"},
        {"sort_seq": 3, "type": "文本", "create_time": 3, "content": "wxid_a: 收到"},
    ]


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def get_messages(self, gid, limit=500, offset=0):
        return list(self.rows)[:limit]


class FakeDriver:
    def __init__(self, rows):
        self.self_id = "wxid_bot"
        self.db = FakeDB(rows)

    def baseline(self, user, lookback=50):
        return 0

    def display_of(self, uid):
        return uid


class FakeLLM:
    def __init__(self, items=None, boom=False):
        self.items = items or []
        self.boom = boom
        self.seen = []

    def chat_json(self, system, user, temperature=0.1):
        if self.boom:
            raise RuntimeError("llm down")
        self.seen.append(user)
        return {"items": list(self.items)}


class FakeWriter:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def add_qa(self, question, answer, context=""):
        if self.fail:
            from answer import KBError
            raise KBError("kb_add_qa: HTTP 500")
        self.calls.append((question, answer, context))
        return "ok"


@pytest.fixture
def bot(tmp_path):
    p = tmp_path / "bot_config.yaml"
    p.write_text(YAML_TMPL % str(tmp_path / "t.db").replace("\\", "/"),
                 encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver(_rows()))
    b._kb_writer = FakeWriter()
    b.build_watch()
    return b


# ---------- KBGateway.add_qa ----------

def test_add_qa_sends_kb_add_qa():
    gw = KBGateway({"mcp": {"url": "http://x/mcp", "api_key": "k", "kb": "team"}})
    seen = []
    gw._call_tool = lambda name, args: seen.append((name, args)) or "ok"
    assert gw.add_qa("q1", "a1", context="来源群：X") == "ok"
    assert seen == [("kb_add_qa", {"kb": "team", "question": "q1",
                                   "answer": "a1", "context": "来源群：X"})]


def test_add_qa_omits_empty_context():
    gw = KBGateway({"mcp": {"url": "http://x/mcp", "api_key": "k", "kb": "team"}})
    seen = []
    gw._call_tool = lambda name, args: seen.append((name, args)) or "ok"
    gw.add_qa("q", "a")
    assert "context" not in seen[0][1]


# ---------- bot._sink_to_kb ----------

def test_sink_extracts_and_pushes(bot):
    bot.personas._llm = FakeLLM(items=[{"q": "手册在哪", "a": "已发"},
                                       {"q": "", "a": "空问题应丢弃"}])
    bot._sink_to_kb(bot.watch[0])
    assert bot._kb_writer.calls == [("手册在哪", "已发", "来源群：老司机养成记")]
    assert bot.store.get_kb_seq(GROUP) == 3


def test_sink_empty_items_advances(bot):
    bot.personas._llm = FakeLLM(items=[])
    bot._sink_to_kb(bot.watch[0])
    assert bot._kb_writer.calls == []
    assert bot.store.get_kb_seq(GROUP) == 3


def test_sink_push_fail_keeps_watermark(bot):
    bot._kb_writer = FakeWriter(fail=True)
    bot.personas._llm = FakeLLM(items=[{"q": "q", "a": "a"}])
    bot._sink_to_kb(bot.watch[0])            # 不抛
    assert bot.store.get_kb_seq(GROUP) == 0


def test_sink_llm_fail_keeps_watermark(bot):
    bot.personas._llm = FakeLLM(boom=True)
    bot._sink_to_kb(bot.watch[0])            # 不抛
    assert bot.store.get_kb_seq(GROUP) == 0
    assert bot._kb_writer.calls == []


# ---------- webui /kb/push_mined ----------

class StubBot:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = {"paused": False, "sent": 0, "start": 0}
        self.kb_writer = FakeWriter()
        self.startup_error = None

    @property
    def driver(self):
        return None

    @property
    def personas(self):
        return None

    def build_watch(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg_path = tmp_path / "bot_config.yaml"
    cfg_path.write_text(YAML_TMPL % str(tmp_path / "w.db").replace("\\", "/"),
                        encoding="utf-8")
    bot = StubBot(BotConfig(str(cfg_path)), Store(str(tmp_path / "w.db")))
    bot.store.add_mined(GROUP, [("问题1", "答案1"), ("问题2", "答案2")])

    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(webui, "threading", types.SimpleNamespace(Thread=_NoThread))
    app = webui.start_web(bot)
    app.testing = True
    return app.test_client(), bot


def test_webui_push_mined(client):
    c, bot = client
    r = c.post("/kb/push_mined", data={"grp": GROUP}, follow_redirects=True)
    assert r.status_code == 200
    assert len(bot.kb_writer.calls) == 2
    q, a, ctx = bot.kb_writer.calls[0]
    assert (q, a, ctx) == ("问题2", "答案2", "来源群：" + GROUP)


def test_webui_push_mined_all_groups(client):
    c, bot = client
    bot.store.add_mined("222@chatroom", [("问题3", "答案3")])
    r = c.post("/kb/push_mined", data={}, follow_redirects=True)
    assert r.status_code == 200
    assert len(bot.kb_writer.calls) == 3
