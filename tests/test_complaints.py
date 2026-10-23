# -*- coding: utf-8 -*-
"""v1.7.4 吐槽收集 + 每日复盘：关键词/引用判定 → complaints 表 → 每晚
LLM 归纳共性+优化建议推主人；#吐槽 即时看原声。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_console import _mk_bot
from test_digest import _FakeBot
from wxbot import digest as digest_mod
from wxbot import group_logic

NOW = time.time()


def _group_w(b):
    return [x for x in b.watch if x["kind"] == "group"][0]


def _msg(content, sender="wxid_a", name="阿强", mtype="文本", seq=None, **kw):
    m = {"type": mtype, "is_self": False, "sender": sender,
         "sender_display": name, "seq": seq or int(time.time()),
         "ts": time.time(), "content": content}
    m.update(kw)
    return m


# ---------- 判定：complaint_hits ----------

def test_complaint_hits_group_needs_bot_mention():
    assert group_logic.complaint_hits("这机器人真蠢，答非所问")
    assert group_logic.complaint_hits("群里AI今天讲了啥") == []        # 提了AI但无吐槽词
    assert group_logic.complaint_hits("老板真蠢") == []                # 没提机器人
    assert group_logic.complaint_hits("智能机器人又回错了")


def test_complaint_hits_quoted_bot_or_nickname():
    assert group_logic.complaint_hits("智能机器人你答的什么垃圾",
                                      bot_names=("智能机器人",))
    assert group_logic.complaint_hits("就这水平？答非所问",
                                      quoted_bot=True)              # 引用机器人消息
    assert group_logic.complaint_hits("就这水平？答非所问") == []


def test_complaint_hits_private_any_word():
    assert group_logic.complaint_hits("你答错了", private=True)
    assert group_logic.complaint_hits("今天好累", private=True) == []


# ---------- store ----------

def test_complaints_store_roundtrip(tmp_path):
    from wxbot.store import Store
    s = Store(str(tmp_path / "t.db"))
    s.add_complaint("group", "群甲", "wxid_a", "阿强", "机器人真蠢", marks="蠢", ts=NOW)
    s.add_complaint("private", "李四", "wxid_b", "李四", "答错了", ts=NOW - 100000)
    rows = s.complaints_unconsumed()
    assert len(rows) == 2 and rows[0]["marks"] == "蠢"
    fresh = s.complaints_unconsumed(since=NOW - 10)
    assert len(fresh) == 1 and fresh[0]["src"] == "group"     # 时间窗过滤
    s.mark_complaints_consumed([r["id"] for r in fresh])
    assert len(s.complaints_unconsumed()) == 1


# ---------- 群聊收集 ----------

def test_group_complaint_collected(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg("这机器人真蠢，答非所问")])
    rows = b.store.complaints_unconsumed()
    assert len(rows) == 1
    assert rows[0]["src"] == "group" and rows[0]["chat"] == "测试群"
    assert "蠢" in rows[0]["marks"] and "答非所问" in rows[0]["marks"]


def test_group_complaint_requires_mention(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg("老板真蠢"), _msg("机器人真棒")])
    assert b.store.complaints_unconsumed() == []


def test_quote_of_bot_collected(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg("就这水平？看不懂", is_quote=True,
                              quoted_user="智能机器人")])
    rows = b.store.complaints_unconsumed()
    assert len(rows) == 1
    # 引用的是别人 → 不收
    b._on_group_msgs(w, [_msg("就这水平？看不懂", is_quote=True,
                              quoted_user="张三")])
    assert len(b.store.complaints_unconsumed()) == 1


# ---------- 私聊/对话台收集 ----------

def test_private_and_console_complaint_collected(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = {"user": "wxid_b", "display": "李四", "kind": "contact", "cfg": {}}
    b._track_complaint(w, _msg("你答错了，回的什么东西"), src="private")
    console = [x for x in b.watch if x["kind"] == "console"][0]
    b._track_complaint(console, _msg("这答案不对吧"), src="console")
    rows = b.store.complaints_unconsumed()
    assert len(rows) == 2
    assert {r["src"] for r in rows} == {"private", "console"}


# ---------- 每日复盘 ----------

def _cr_bot(tmp_path, enabled=True):
    bot = _FakeBot(tmp_path)
    bot.cfg.data["app"]["complaint_review"] = dict(
        {"enabled": enabled, "hour": 21, "push_to": "文件传输助手"})
    return bot


def _digest_rows(bot):
    return [r for r in bot.store.recent_logs(60) if r["kind"] == "digest"]


def test_complaint_review_pushes_summary(tmp_path):
    b = _cr_bot(tmp_path)

    def _sum(g, lines, ins):
        assert "吐槽" in ins                                  # 提示词针对吐槽复盘
        return "共性问题：答非所问。建议：补群知识库"

    b.personas.summarize_day = _sum
    b.store.add_complaint("group", "群甲", "wxid_a", "阿强",
                          "机器人真蠢答非所问", marks="蠢、答非所问")
    b.store.add_complaint("group", "群甲", "wxid_c", "老王",
                          "AI回错人了", marks="回错")
    digest_mod.run_complaint_review(b)
    pushes = [r for r in _digest_rows(b) if "吐槽复盘" in (r["detail"] or "")]
    assert len(pushes) == 1
    d = pushes[0]["detail"]
    assert "近24h 2 条" in d and "阿强" in d and "建议" in d
    assert b.store.complaints_unconsumed() == []            # 推送后消化


def test_complaint_review_llm_failure_falls_back(tmp_path):
    b = _cr_bot(tmp_path)

    def _boom(group, lines, instruction):
        raise RuntimeError("LLM 挂了")

    b.personas.summarize_day = _boom
    b.store.add_complaint("group", "群甲", "wxid_a", "阿强",
                          "机器人真蠢", marks="蠢")
    digest_mod.run_complaint_review(b)
    pushes = [r for r in _digest_rows(b) if "吐槽复盘" in (r["detail"] or "")]
    assert pushes and "[简版]" in pushes[0]["detail"]
    assert "蠢×1" in pushes[0]["detail"]


def test_complaint_review_disabled_or_empty(tmp_path):
    b = _cr_bot(tmp_path, enabled=False)
    b.store.add_complaint("group", "群甲", "wxid_a", "阿强", "真蠢", marks="蠢")
    digest_mod.run_complaint_review(b)
    assert not [r for r in _digest_rows(b) if "吐槽复盘" in (r["detail"] or "")]

    (tmp_path / "b2").mkdir()                                # 独立库，别共 db
    b2 = _cr_bot(tmp_path / "b2")                            # 开着但没吐槽
    digest_mod.run_complaint_review(b2)
    assert not [r for r in _digest_rows(b2) if "吐槽复盘" in (r["detail"] or "")]


# ---------- #吐槽 命令 ----------

def test_report_complaints_now(tmp_path):
    b = _cr_bot(tmp_path)
    assert "没有" in digest_mod.report_complaints_now(b) or \
        "还没收集到" in digest_mod.report_complaints_now(b)
    b.store.add_complaint("group", "群甲", "wxid_a", "阿强", "机器人真蠢", marks="蠢")
    text = digest_mod.report_complaints_now(b)
    assert "阿强" in text and "共 1 条" in text
    assert len(b.store.complaints_unconsumed()) == 1        # 不清空缓存


def test_admin_cmd_tuso(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    r = b._do_admin("吐槽", [])
    assert "吐槽" in r
