# -*- coding: utf-8 -*-
"""R2 主题库：store 主题表 + LLM 聚类归簇 + 群答疑本地主题先撞。"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import themes as themes_mod
from wxbot.store import Store


def _mk_store(tmp_path):
    return Store(str(tmp_path / "t.db"))


# ---------- store：主题表 ----------

def test_theme_roundtrip(tmp_path):
    s = _mk_store(tmp_path)
    s.add_mined("111@chatroom", [("怎么续费物业费", "去物业处缴"),
                                 ("门禁卡哪里办", "物业前台带身份证")])
    s.add_mined("222@chatroom", [("别的群的问题", "别的答案")])
    assert len(s.mined_unthemed("111@chatroom")) == 2
    qa = s.mined_pairs("111@chatroom")
    s.set_theme(qa[0]["id"], "物业手续")
    s.set_theme(qa[0]["id"], "物业手续")            # 幂等
    s.set_theme(qa[1]["id"], "物业手续")
    assert s.mined_unthemed("111@chatroom") == []
    assert s.themes_list("111@chatroom") == [{"theme": "物业手续", "n": 2}]
    by_id = {r["id"]: r for r in s.mined_pairs("111@chatroom")}
    assert by_id[qa[0]["id"]]["theme"] == "物业手续"
    # 其它群不受影响
    assert len(s.mined_unthemed("222@chatroom")) == 1


# ---------- 归簇 ----------

class _ThemeLLM:
    def __init__(self):
        self.calls = []

    def assign_themes(self, existing, pairs):
        self.calls.append((list(existing), list(pairs)))
        return {"items": [{"id": p[0], "theme": "物业手续"} for p in pairs]}


class _BoomLLM:
    def assign_themes(self, existing, pairs):
        raise RuntimeError("LLM 挂了")


def _mk_bot(tmp_path, llm):
    return type("B", (), {
        "store": _mk_store(tmp_path),
        "personas": llm,
    })()


def test_assign_batch(tmp_path):
    s = _mk_store(tmp_path)
    s.add_mined("111@chatroom", [("怎么续费", "物业处缴"), ("门禁卡哪办", "前台"),
                                 ("地铁几号线", "12号线")])
    bot = _mk_bot(tmp_path, _ThemeLLM())
    n = themes_mod.assign(bot, "111@chatroom")
    assert n == 3
    assert s.themes_list("111@chatroom") == [{"theme": "物业手续", "n": 3}]
    assert themes_mod.assign(bot, "111@chatroom") == 0     # 没有未归簇的了


def test_assign_llm_failure_isolated(tmp_path):
    s = _mk_store(tmp_path)
    s.add_mined("111@chatroom", [("怎么续费", "物业处缴")])
    bot = _mk_bot(tmp_path, _BoomLLM())
    assert themes_mod.assign(bot, "111@chatroom") == 0
    assert len(s.mined_unthemed("111@chatroom")) == 1      # 原样保留待重试


# ---------- 群答疑本地先撞 ----------

def _bigrams(text):
    return themes_mod.q_bigrams(text)


def test_q_bigrams():
    assert "停水" in _bigrams("停水到几点")
    assert _bigrams("停水") == {"停水"}
    assert _bigrams("a b") == set()


def test_local_hit_threshold():
    mined = [("停水通知什么时候来水", "明早7点到11点"),
             ("今天天气不错", "是的")]
    hit = themes_mod.local_hit(mined, "什么时候来水")
    assert hit is not None and "7点" in hit[1]
    assert themes_mod.local_hit(mined, "附近哪家火锅好吃") is None


# ---------- 群答疑本地先撞接线 ----------

def _at_msg(content):
    return {"type": "文本", "is_self": False, "sender": "wxid_a",
            "sender_display": "阿强", "seq": 9, "ts": time.time(),
            "content": content, "at_users": ["wxid_bot"]}


def test_group_qa_local_hit_answers_without_kb(tmp_path, monkeypatch):
    from test_console import _mk_bot
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "group"][0]
    b.store.add_mined("111@chatroom", [("停水通知什么时候来水", "明早7点到11点")])

    class _BoomKB:
        def ask(self, q, d):
            raise AssertionError("本地命中不应再查 KB")

    b._kb = _BoomKB()
    seen = {}
    monkeypatch.setattr(b.personas, "group_answer",
                        lambda *a, **kw: seen.update(kw) or "明早7点来水")
    b._group_qa(w, _at_msg("@智能机器人 什么时候来水"))
    assert "沉淀" in (seen.get("kb_ref") or "")
    qa_logs = [r for r in b.store.recent_logs(20) if r["kind"] == "qa"]
    assert qa_logs and "群内沉淀" in qa_logs[0]["detail"] and "7点" in qa_logs[0]["detail"]


def test_group_qa_local_disabled_goes_kb(tmp_path, monkeypatch):
    from test_console import _mk_bot
    b = _mk_bot(tmp_path)
    b.cfg.data["groups"][0].setdefault("qa", {})["local_first"] = False
    b.build_watch()
    w = [x for x in b.watch if x["kind"] == "group"][0]
    b.store.add_mined("111@chatroom", [("停水通知什么时候来水", "明早7点到11点")])

    class _KB:
        def ask(self, q, d):
            return ("问物业最准", True, False)

    monkeypatch.setattr(type(b), "kb", property(lambda self: _KB()))
    monkeypatch.setattr(b.personas, "understand_question",
                        lambda g, ctx, q, a, p="": {"standalone": q, "should_answer": True})
    seen = {}

    def _ga(*a, **kw):
        seen.update(kw)
        return "走知识库的回答"

    monkeypatch.setattr(b.personas, "group_answer", _ga)
    b._group_qa(w, _at_msg("@智能机器人 什么时候来水"))
    assert not (seen.get("kb_ref") or "")
    qa_logs = [r for r in b.store.recent_logs(20) if r["kind"] == "qa"]
    assert qa_logs and "群内沉淀" not in qa_logs[0]["detail"]
