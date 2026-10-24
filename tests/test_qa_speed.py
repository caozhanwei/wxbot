# -*- coding: utf-8 -*-
"""群答疑提速：提问理解与 KB 预取并行、轮询默认加密、LLM 空回复重试加大预算。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.bot import Bot, POLL_SEC_DEFAULT
from wxbot.config import BotConfig


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
        return []

    def send_text(self, display, text):
        return True, "ok"


class SlowLLM:
    """提问理解耗时 0.4s（真实网关往返的缩影）。"""

    def __init__(self, hold=0.4, standalone=""):
        self.hold = hold
        self.standalone = standalone

    def chat_json(self, system, user, temperature=0.1):
        time.sleep(self.hold)
        return {"standalone": self.standalone, "should_answer": True}

    def chat(self, system, user, temperature=0.8):
        return "好的"


class RaceKB:
    """kb.ask 耗时 hold 秒，记录每次调用时间轴。"""

    def __init__(self, hold=0.4):
        self.hold = hold
        self.asked = []

    def ask(self, question, room=""):
        self.asked.append(question)
        time.sleep(self.hold)
        return "KB答案", True, True


def _make_bot(tmp_path, kb, llm=None):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())
    b._kb = kb
    b.personas._llm = llm or SlowLLM()
    b.build_watch()
    return b


def _msg(content, at_users=None):
    return {"type": "文本", "is_self": False, "sender": "wxid_a",
            "sender_display": "阿强", "seq": 10, "ts": 1,
            "content": content, "at_users": at_users or []}


def test_kb_prefetch_runs_parallel_with_understand(tmp_path):
    """@提问时 KB 预取与提问理解并发：总耗时≈单次而非两次串行。"""
    kb = RaceKB(hold=0.4)
    b = _make_bot(tmp_path, kb, SlowLLM(hold=0.4))
    t0 = time.time()
    b._group_qa(b.watch[0], _msg("@伟 NETLIST_FILE 怎么配", ["wxid_bot"]))
    elapsed = time.time() - t0
    assert kb.asked == ["NETLIST_FILE 怎么配"]
    # 串行 ≈0.8s+；并行应 ≈0.5s（留 0.3s 调度余量）
    assert elapsed < 0.7, elapsed


def test_kb_rerun_when_question_rewritten(tmp_path):
    """提问理解改写了问题且与原文不同时，用改写后的独立问题再查 KB。"""
    kb = RaceKB(hold=0.01)
    b = _make_bot(tmp_path, kb,
                  SlowLLM(hold=0.0, standalone="RCE 的 NETLIST_FILE 参数如何配置"))
    b._group_qa(b.watch[0], _msg("@伟 它怎么配", ["wxid_bot"]))
    assert kb.asked == ["它怎么配", "RCE 的 NETLIST_FILE 参数如何配置"]
    hit_recs = [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]
    assert hit_recs and "KB答案" in hit_recs[0]["detail"]


def test_kb_no_rerun_when_unchanged(tmp_path):
    """改写后问题与原问题相同（strip 后）：只查一次 KB。"""
    kb = RaceKB(hold=0.01)
    b = _make_bot(tmp_path, kb, SlowLLM(hold=0.0, standalone="NETLIST_FILE 怎么配"))
    b._group_qa(b.watch[0], _msg("@伟 NETLIST_FILE 怎么配", ["wxid_bot"]))
    assert kb.asked == ["NETLIST_FILE 怎么配"]


def test_kb_prefetch_failure_falls_back(tmp_path):
    """预取挂了不致命：等同 KB 失败，走 llm_fallback 兜底照常回复。"""
    class BoomKB:
        def ask(self, question, room=""):
            raise RuntimeError("gateway hiccup")

    db = str(tmp_path / "t.db").replace("\\", "/")
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path).replace(
        "qa: {enabled: true, grounded_reply: true}",
        'qa: {enabled: true, grounded_reply: true, llm_fallback: true}'), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())
    b._kb = BoomKB()
    b.personas._llm = SlowLLM(hold=0.0)
    b.build_watch()
    b._group_qa(b.watch[0], _msg("@伟 NETLIST_FILE 怎么配", ["wxid_bot"]))
    recs = [r for r in b.store.recent_logs(50) if r["kind"] == "qa"]
    assert recs and "好的" in recs[0]["detail"]   # LLM 兜底回复已发出


# ---------- 默认轮询间隔 ----------

def test_poll_default_1s():
    assert float(POLL_SEC_DEFAULT) == 1.0


# ---------- LLM 空回复重试加大预算 ----------

def test_llm_retry_escalates_max_tokens():
    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": ""}}]}

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["max_tokens"])
        return FakeResp()

    import llm as llm_mod
    orig = llm_mod.requests.post
    llm_mod.requests.post = fake_post
    try:
        from llm import LLM
        c = LLM("custom", "k", "m", base_url="http://x/v1", timeout=5)
        with pytest.raises(Exception):
            c.chat("s", "u", max_tokens=2000)
        assert calls[0] == 2000
        assert calls[1] >= 4000              # 重试预算翻倍，防 reasoning 吃空
        assert len(calls) == 2
    finally:
        llm_mod.requests.post = orig
