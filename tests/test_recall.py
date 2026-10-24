# -*- coding: utf-8 -*-
"""R2 跨聊天问答：关键词提取（LLM+朴素兜底）→ 全启用会话检索 → 带出处 LLM 整合回答。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import recall as recall_mod
from wxbot.store import Store

NOW = time.time()
DAY = 86400


def _row(sender, nick, content, age_sec, seq, mtype="文本"):
    return {"sort_seq": seq, "create_time": NOW - age_sec,
            "sender_username": sender, "sender_nickname": nick,
            "type": mtype, "content": content, "local_id": seq, "source": ""}


ROWS = {
    "111@chatroom": [
        _row("wxid_a", "阿强", "wxid_a:\n问下停水到几点", 2 * DAY, 5),
        _row("wxid_b", "李四", "wxid_b:\n停水通知贴在物业门口了", 1 * DAY, 6),
        _row("wxid_a", "阿强", "wxid_a:\n今天天气不错", 3 * DAY, 4),
        _row("wxid_a", "阿强", "wxid_a:\n很久以前的停水讨论", 60 * DAY, 3),
        _row("wxid_a", "阿强", "wxid_a:\n<?xml version='1.0'?>", 1 * DAY, 7),
        _row("wxid_a", "阿强", "wxid_a:\n[图片]", 1 * DAY, 8, mtype="图片"),
    ],
    "wxid_lin": [
        _row("wxid_lin", "林哥", "停水那天记得接水", 2 * DAY, 9),
    ],
    "222@chatroom": [
        _row("wxid_x", "路人", "wxid_x:\n别的群不相关", 1 * DAY, 10),
    ],
    "filehelper": [
        _row("wxid_me", "我", "刚才说错了，是明天停水", 300, 20),
        _row("wxid_me", "我", "之前问过停水到几点", 900, 19),
    ],
}


class _DB:
    def get_messages(self, user, limit=500, offset=0):
        return list(ROWS.get(user, []))[:limit]


class _Driver:
    self_id = "wxid_bot"
    self_name = "智能机器人"
    db = _DB()

    def display_of(self, uid):
        return {"wxid_a": "阿强", "wxid_b": "李四", "wxid_lin": "林哥",
                "wxid_x": "路人"}.get(uid) or uid


class _LLM:
    def __init__(self):
        self.terms_q = None
        self.answer_args = None
        self.boom_terms = False
        self.boom_answer = False

    def extract_recall_terms(self, question):
        if self.boom_terms:
            raise RuntimeError("LLM 挂了")
        self.terms_q = question
        return {"keywords": ["停水"], "who": "", "days": 30}

    def chat_recall(self, question, lines, recent=None):
        if self.boom_answer:
            raise RuntimeError("LLM 挂了")
        self.answer_args = (question, list(lines), list(recent or []))
        return "阿强在【群甲】前天问过停水到几点（出自群聊记录）"


def _mk_bot(tmp_path, watch=None, llm=None):
    llm = llm or _LLM()
    bot = type("B", (), {
        "store": Store(str(tmp_path / "t.db")),
        "driver": _Driver(),
        "personas": llm,
        "cfg": type("C", (), {"data": {"app": {}}})(),
        "watch": watch if watch is not None else [
            {"user": "111@chatroom", "display": "群甲", "kind": "group", "cfg": {}},
            {"user": "wxid_lin", "display": "林哥", "kind": "contact", "cfg": {}},
            {"user": "222@chatroom", "display": "群乙", "kind": "group", "cfg": {}},
            {"user": "filehelper", "display": "文件传输助手", "kind": "console", "cfg": {}},
        ],
    })()
    return bot, llm


# ---------- 检索 ----------

def test_search_filters_and_rank(tmp_path):
    bot, _ = _mk_bot(tmp_path)
    rows = recall_mod.search_history(bot, {"keywords": ["停水", "物业"], "who": "",
                                           "days": 30})
    texts = [r["content"] for r in rows]
    # 命中：两群停水相关 3 条（xml/图片/过气60天/别的群 不算）
    assert len(rows) == 3
    assert any("贴在物业门口" in t for t in texts)      # 停水+物业 双词命中排最前
    assert "物业门口" in rows[0]["content"]
    assert all(r["chat"] in ("群甲", "林哥") for r in rows)
    assert all(r.get("sender_display") for r in rows)


def test_search_who_filter(tmp_path):
    bot, _ = _mk_bot(tmp_path)
    rows = recall_mod.search_history(bot, {"keywords": ["停水"], "who": "林哥",
                                           "days": 30})
    assert len(rows) == 1 and rows[0]["chat"] == "林哥"


# ---------- 问答 ----------

def test_answer_with_attribution(tmp_path):
    bot, llm = _mk_bot(tmp_path)
    out = recall_mod.answer(bot, "谁说过停水的事")
    assert "阿强" in out
    q, lines, recent = llm.answer_args
    assert q == "谁说过停水的事"
    assert any("【群甲】" in l and "阿强" in l for l in lines)


def test_answer_no_hit_short_circuit(tmp_path):
    bot, llm = _mk_bot(tmp_path)
    llm.extract_recall_terms = lambda q: {"keywords": ["不存在词"], "who": "",
                                          "days": 7}
    out = recall_mod.answer(bot, "随便问问")
    assert "没查到" in out
    assert llm.answer_args is None              # 零命中不调 LLM 硬凑


def test_answer_llm_failure_falls_back(tmp_path):
    bot, llm = _mk_bot(tmp_path)
    llm.boom_answer = True
    out = recall_mod.answer(bot, "谁说过停水")
    assert "【群甲】" in out                     # 机械列出出处行兜底


def test_extract_terms_naive_fallback(tmp_path):
    bot, llm = _mk_bot(tmp_path)
    llm.boom_terms = True
    terms = recall_mod.extract_terms(bot, "群里谁问过 停水 的事？")
    assert "停水" in terms["keywords"] and terms["days"] >= 1
    assert terms["who"] == ""


# ---------- 以最新为准：记录新到旧 + 对话台最近往来注入 ----------

def test_answer_lines_newest_first(tmp_path):
    """渲染给 LLM 的记录按时间新到旧：最新的那条排最前（不再关键词多者霸屏）。"""
    bot, llm = _mk_bot(tmp_path)
    recall_mod.answer(bot, "谁说过停水的事")
    _, lines, _ = llm.answer_args
    assert "物业门口" in lines[0]          # 1 天前的最新记录排第一
    assert all("很久以前" not in l for l in lines)


def test_answer_injects_recent_console_dialogue(tmp_path):
    """对话台最近往来（号主刚说的纠正）必须进上下文，且最新一条排最前。"""
    bot, llm = _mk_bot(tmp_path)
    recall_mod.answer(bot, "谁说过停水的事")
    _, _, recent = llm.answer_args
    assert recent and "明天停水" in recent[0]


class _Cfg:
    llm_cfg = {}

    def persona(self, name):
        return {"style": "简洁口语化", "background": ""}


def test_chat_recall_prompt_prioritizes_latest():
    """Prompt 明说：当前问题最高优先，旧记录冲突时以号主最新说法为准。"""
    from wxbot import personas
    e = personas.PersonaEngine(_Cfg())
    seen = {}

    class FL:
        def chat(self, system, user, temperature=0.3):
            seen["system"], seen["user"] = system, user
            return "明天停水，号主刚纠正过"

    e._llm = FL()
    out = e.chat_recall("到底哪天停水",
                        ["【群甲】阿强(10-01 12:00): 停水到几点"],
                        recent=["(刚刚) 刚才说错了，是明天停水"])
    assert "明天停水" in out
    assert "最高优先" in seen["system"]
    assert "以号主最新说法为准" in seen["system"]
    assert "最近的对话台往来" in seen["user"] and "明天停水" in seen["user"]
    assert "新到旧" in seen["user"]
