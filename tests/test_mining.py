# -*- coding: utf-8 -*-
"""mining 模块：解析/分窗/翻页/存储水位/导出。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import mining
from wxbot.store import Store


class FakeDB:
    """get_messages 降序分页的假库（seq 1..N）。"""

    def __init__(self, n):
        self.rows = [{"sort_seq": i, "type": "文本",
                      "content": f"wxid_{i % 3}: 消息{i}",
                      "create_time": 1700000000 + i} for i in range(1, n + 1)]
        self.calls = []

    def get_messages(self, user, limit=20, offset=0):
        self.calls.append((limit, offset))
        return list(reversed(self.rows))[offset:offset + limit]


class FakeDriver:
    def __init__(self, n):
        self.db = FakeDB(n)

    def display_of(self, uid):
        return {"wxid_0": "张三", "wxid_1": "李四"}.get(uid, uid)


def test_parse_pairs_fenced_and_junk():
    assert mining._parse_pairs('```json\n[{"q":"a?","a":"b"}]\n```') == [("a?", "b")]
    assert mining._parse_pairs('前置废话 [{"q":"a?","a":"b"}] 后置') == [("a?", "b")]
    assert mining._parse_pairs("[]") == []
    assert mining._parse_pairs("") == []
    assert mining._parse_pairs("not json at all") == []
    assert mining._parse_pairs('[{"q":"","a":"x"}]') == []
    assert mining._parse_pairs('[{"q":"x","a":"y"},"junk"]') == [("x", "y")]
    assert mining._parse_pairs('[{"q":"q" * 300,"a":"ok"}]') == []


def test_windows_overlap():
    items = list(range(100))
    wins = list(mining._windows(items))
    assert wins[0][0] == 0 and len(wins[0]) == mining._WINDOW
    step = mining._WINDOW - mining._OVERLAP
    assert wins[1][0] == step
    covered = {x for w in wins for x in w}
    assert covered == set(items)


def test_fetch_history_full_pages_to_end():
    d = FakeDriver(1200)
    msgs = mining._fetch_history(d, "g@chatroom", 0)
    assert [m["sort_seq"] for m in msgs] == list(range(1, 1201))
    assert len(d.db.calls) == 3   # 500+500+200


def test_fetch_history_incremental_stops_at_watermark():
    d = FakeDriver(1200)
    msgs = mining._fetch_history(d, "g@chatroom", 1000)
    assert [m["sort_seq"] for m in msgs] == list(range(1001, 1201))


def test_normalize_filters_and_names():
    d = FakeDriver(1)
    rows = [{"sort_seq": 5, "type": "文本", "content": "wxid_0: 问题内容",
             "create_time": 123},
            {"sort_seq": 6, "type": "图片", "content": "wxid_0: x",
             "create_time": 124},
            {"sort_seq": 7, "type": "文本", "content": "<?xml blah",
             "create_time": 125},
            {"sort_seq": 8, "type": "文本", "content": "wxid_0: 卡号622202020011223345",
             "create_time": 126}]
    out = mining._normalize(d, rows)
    assert len(out) == 1
    assert out[0]["line"] == "张三: 问题内容"
    assert out[0]["seq"] == 5


def test_store_mined_roundtrip(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert s.get_mined_seq("g@chatroom") == 0
    s.set_mined_seq("g@chatroom", 42)
    assert s.get_mined_seq("g@chatroom") == 42
    s.set_mined_seq("g@chatroom", 50)
    assert s.get_mined_seq("g@chatroom") == 50
    s.add_mined("g@chatroom", [("q1", "a1"), ("q2", "a2")])
    s.add_mined("g@chatroom", [("q3", "a3")])
    assert s.mined_count() == 3
    assert s.mined_count("g@chatroom") == 3
    assert s.mined_count("other") == 0
    pairs = {(r["question"], r["answer"]) for r in s.mined_pairs()}
    assert pairs == {("q1", "a1"), ("q2", "a2"), ("q3", "a3")}


def test_export_markdown(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert mining.export_markdown(s, str(tmp_path)) is None
    s.add_mined("群A", [("怎么配环境？", "装 3.12 然后 pip install")])
    s.add_mined("群B", [("价格多少？", "100 元")])
    path = mining.export_markdown(s, str(tmp_path))
    assert path and os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    assert "怎么配环境？" in txt and "群A" in txt and "群B" in txt


def test_do_mine_end_to_end(tmp_path, monkeypatch):
    """假 LLM 返回一对问答，走完整 _do_mine（含水位+去重）。"""
    s = Store(str(tmp_path / "t.db"))
    d = FakeDriver(10)
    calls = {"n": 0}

    class FakeLLM:
        def chat(self, system, user, temperature=0.2):
            calls["n"] += 1
            return '[{"q": "群里问的问题？", "a": "群里给的答案"}]'

    class FakePersonas:
        llm = FakeLLM()

    class FakeBot:
        store, driver, personas = s, d, FakePersonas()

    mining._do_mine(FakeBot(), "g@chatroom", "测试群", full=True, auto=False)
    assert calls["n"] >= 1
    assert s.mined_count("g@chatroom") == 1
    assert s.get_mined_seq("g@chatroom") == 10
    # 再跑一次增量：没有新消息，不重复入库
    mining._do_mine(FakeBot(), "g@chatroom", "测试群", full=False, auto=False)
    assert s.mined_count("g@chatroom") == 1
    assert mining.status()["error"] == ""
