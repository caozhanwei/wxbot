# -*- coding: utf-8 -*-
"""免@上下文回复：触发、冷却节流、KB 失败降级、LLM 判定不回、@仍走原答疑链路。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot.bot import Bot
from wxbot.config import BotConfig

GROUP = "111@chatroom"


def _cfg_text(tmp_path, no_at=True):
    db = str(tmp_path / "t.db").replace("\\", "/")
    text = """
app: {dry_run: true, db_file: "%s"}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
groups:
  - id: "111@chatroom"
    display: 老司机养成记
    enabled: true
    no_at_reply: %s
    qa: {enabled: true, grounded_reply: true}
    manage: {welcome: false, forbidden_words: [], warn_persona: 群管家-热心, warn_threshold: 2}
""" % (db, "true" if no_at else "false")
    return text


def _rows():
    return [
        {"sort_seq": 1, "type": "文本", "create_time": 1,
         "content": "wxid_a: 有人知道 NETLIST_FILE 怎么配吗"},
        {"sort_seq": 2, "type": "文本", "create_time": 2,
         "content": "wxid_b: 我的卡号 6222021234567890123 别外传"},
        {"sort_seq": 3, "type": "文本", "create_time": 3,
         "content": "wxid_a: 求一份用户手册"},
    ]


class FakeDB:
    """driver.db 替身：返回群原始行（content 带 wxid 前缀，同 4.x 本地库）。"""

    def __init__(self, rows):
        self.rows = rows

    def get_messages(self, gid, limit=500, offset=0):
        return list(self.rows)[:limit]


class FakeDriver:
    def __init__(self, rows):
        self.self_id = "wxid_bot"
        self.self_name = "群助手"
        self.db = FakeDB(rows)
        self.sent = []

    def baseline(self, user, lookback=50):
        return 0

    def mention_names(self, group_cfg):
        return ["群助手"]

    def display_of(self, uid):
        return {"wxid_a": "阿强", "wxid_b": "老王", "wxid_bot": "群助手"}.get(uid) or uid

    def build_context(self, user_id, limit=16, speaker_map=None):
        return ["阿强: 有人知道 NETLIST_FILE 怎么配吗",
                "老王: 帮顶",
                "阿强: 求一份用户手册"]

    def send_text(self, display, text):
        self.sent.append((display, text))
        return True, "ok"


class FakeLLM:
    def __init__(self, verdict=True, reply="NETLIST_FILE 指定网表输出路径，手册我发你"):
        self.verdict = verdict
        self.reply = reply
        self.judged = 0
        self.chatted = 0

    def chat_json(self, system, user, temperature=0.1):
        self.judged += 1
        return {"reply": self.verdict, "reason": "有人提问"}

    def chat(self, system, user, temperature=0.8):
        self.chatted += 1
        return self.reply


class FakeKB:
    def __init__(self):
        self.asked = []
        self.searched = []

    def ask(self, question, room=""):
        self.asked.append(question)
        return "NETLIST_FILE 用于指定输出网表路径。", True, True

    def search(self, query, top_k=5):
        self.searched.append(query)
        return "参考：NETLIST_FILE 指定输出网表文件路径。"


class FakeWriter:
    def __init__(self, fail=False):
        self.fail = fail
        self.uploads = []

    def upload_markdown(self, title, markdown):
        if self.fail:
            from answer import KBError
            raise KBError("HTTP 500: gateway down")
        self.uploads.append((title, markdown))
        return "ok"


@pytest.fixture
def bot(tmp_path):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver(_rows()))
    b._kb = FakeKB()
    b._kb_writer = FakeWriter()
    b.build_watch()
    return b


def _msg(content, sender="wxid_a", name="阿强", seq=10):
    return {"type": "文本", "is_self": False, "sender": sender,
            "sender_display": name, "content": content, "seq": seq, "ts": 1}


def _w(bot):
    return bot.watch[0]


def _logs(bot, kind):
    return [r for r in bot.store.recent_logs(50) if r["kind"] == kind]


def test_no_at_triggers_reply_and_sinks(bot):
    llm = FakeLLM()
    bot.personas._llm = llm
    bot._on_group_msgs(_w(bot), [_msg("求一份用户手册")])
    assert llm.judged == 1 and llm.chatted == 1
    recs = _logs(bot, "no_at")
    assert len(recs) == 1 and "NETLIST_FILE" in recs[0]["detail"]
    # 沉淀：3 行原始 → _SENSITIVE 滤掉卡号行 → 剩 2 条
    assert len(bot._kb_writer.uploads) == 1
    title, md = bot._kb_writer.uploads[0]
    assert "老司机养成记" in title
    assert "阿强" in md and "求一份用户手册" in md
    assert "6222021234567890123" not in md and "老王" not in md
    # 回复前确实检索了知识库参考
    assert bot._kb.searched
    # 水位线推进；冷却已登记
    assert bot.store.get_kb_seq(GROUP) == 3
    assert bot.state["no_at_last"][GROUP] > 0


def test_no_at_cooldown_throttles(bot):
    llm = FakeLLM()
    bot.personas._llm = llm
    bot._on_group_msgs(_w(bot), [_msg("求一份用户手册")])
    assert llm.judged == 1
    bot._on_group_msgs(_w(bot), [_msg("还有吗", seq=11)])
    assert llm.judged == 1                      # 冷却期内：连判定都不做
    assert len(_logs(bot, "no_at")) == 1
    bot.state["no_at_last"][GROUP] = 0          # 冷却结束
    bot._on_group_msgs(_w(bot), [_msg("还有吗", seq=12)])
    assert llm.judged == 2 and len(_logs(bot, "no_at")) == 2


def test_kb_failure_degrades_silently(bot):
    bot._kb_writer = FakeWriter(fail=True)

    class Boom:
        def ask(self, q, room=""):
            raise RuntimeError("kb down")

        def search(self, q, top_k=5):
            raise RuntimeError("kb down")

    bot._kb = Boom()
    llm = FakeLLM()
    bot.personas._llm = llm
    bot._on_group_msgs(_w(bot), [_msg("求一份用户手册")])   # 不崩
    # KB 读写全挂：回复照发、无参考也作答
    assert llm.chatted == 1
    recs = _logs(bot, "no_at")
    assert len(recs) == 1 and "NETLIST_FILE" in recs[0]["detail"]
    # 上传失败水位线不前移，留待下次重试
    assert bot.store.get_kb_seq(GROUP) == 0


def test_judge_decline_keeps_silent(bot):
    llm = FakeLLM(verdict=False)
    bot.personas._llm = llm
    bot._on_group_msgs(_w(bot), [_msg("哈哈哈哈")])
    assert llm.judged == 1 and llm.chatted == 0
    assert not _logs(bot, "no_at")
    assert bot.state["no_at_last"].get(GROUP, 0) == 0   # 判定不回不占冷却
    # 紧接着的真问题仍会处理
    llm.verdict = True
    bot._on_group_msgs(_w(bot), [_msg("求一份用户手册")])
    assert llm.chatted == 1


def test_at_bot_still_goes_qa(bot):
    llm = FakeLLM()
    bot.personas._llm = llm
    bot.state["no_at_last"][GROUP] = time.time() + 999   # 免@已在冷却中
    bot._on_group_msgs(_w(bot), [_msg("@群助手 NETLIST_FILE 怎么配")])
    assert llm.judged == 1 and llm.chatted == 0          # @路径只走"提问理解"，不触发免@判定/人设
    assert bot._kb.asked == ["NETLIST_FILE 怎么配"]       # 走了 KB 答疑
    recs = _logs(bot, "qa")
    assert recs and "NETLIST_FILE 用于指定输出网表路径" in recs[0]["detail"]


def test_disabled_group_never_triggers(tmp_path):
    p = tmp_path / "bot_config.yaml"
    p.write_text(_cfg_text(tmp_path, no_at=False), encoding="utf-8")
    b = Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver(_rows()))
    b._kb = FakeKB()
    b._kb_writer = FakeWriter()
    b.build_watch()
    llm = FakeLLM()
    b.personas._llm = llm
    b._on_group_msgs(b.watch[0], [_msg("求一份用户手册")])
    assert llm.judged == 0
    assert not b._kb_writer.uploads
