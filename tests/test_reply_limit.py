# -*- coding: utf-8 -*-
"""群回复字数上限（max_reply_chars）：limit_chars 截断 / prompt 注入 / bot 落点 / webui 保存回读。"""
import os
import sys
import time
import types

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import bot as bot_mod
from wxbot import webui
from wxbot.config import BotConfig
from wxbot.personas import PersonaEngine, limit_chars
from wxbot.store import Store


# ---------- limit_chars 截断 ----------
def test_limit_short_untouched():
    assert limit_chars("你好呀", 10) == "你好呀"


def test_limit_zero_or_invalid_means_off():
    t = "这句话很长很长很长很长很长很长很长很长很长很长"
    assert limit_chars(t, 0) == t
    assert limit_chars(t, None) == t
    assert limit_chars(t, "abc") == t


def test_limit_cuts_at_sentence_boundary():
    t = "这是第一句。这是第二句！这是第三句？"
    assert limit_chars(t, 8) == "这是第一句。"


def test_limit_hard_cut_without_boundary():
    assert limit_chars("一二三四五六七八九十", 5) == "一二三四五"


def test_limit_floor_avoids_too_short_cut():
    # 句边界只出现在前半段（低于地板线）→ 不腰斩到没法读，按上限硬截
    assert limit_chars("一。二三四五六七八九十", 6) == "一。二三四五"


def test_limit_strips_dangling_comma():
    assert limit_chars("明天见，我把东西带过去哈", 4) == "明天见"
    assert limit_chars("好的一言为定，明天见哈", 8) == "好的一言为定"


# ---------- prompt 注入 ----------
class _FakeLLM:
    def __init__(self):
        self.seen = []

    def chat(self, system, user, temperature=0.8):
        self.seen.append(system)
        return "好嘞收到"

    def chat_json(self, system, user, temperature=0.0):
        return {}


class _FakeCfg:
    llm_cfg = {"temperature": 0.8}

    def persona(self, name):
        return {"style": "幽默", "background": "测试"}


def _engine():
    e = PersonaEngine(_FakeCfg())
    e.llm = _FakeLLM()
    return e


def test_group_answer_prompt_default_200():
    e = _engine()
    e.group_answer("p", "群", ["甲: hi"], "问", "甲")
    assert "不超过 200 字" in e.llm.seen[-1]


def test_group_answer_prompt_max_chars():
    e = _engine()
    e.group_answer("p", "群", ["甲: hi"], "问", "甲", max_chars=60)
    assert "不超过 60 字" in e.llm.seen[-1]
    assert "不超过 200 字" not in e.llm.seen[-1]


def test_context_reply_prompt_max_chars():
    e = _engine()
    e.context_reply("p", "群", ["甲: hi"], "")
    assert "不超过 120 字" in e.llm.seen[-1]
    e.llm.seen.clear()
    e.context_reply("p", "群", ["甲: hi"], "", max_chars=40)
    assert "不超过 40 字" in e.llm.seen[-1]


def test_casual_reply_prompt_max_chars():
    e = _engine()
    e.casual_group_reply("p", "群", ["甲: hi"], "甲", "哈哈")
    assert "不要超过 50 字" in e.llm.seen[-1]
    e.llm.seen.clear()
    e.casual_group_reply("p", "群", ["甲: hi"], "甲", "哈哈", max_chars=30)
    assert "不要超过 30 字" in e.llm.seen[-1]


# ---------- bot 落点 ----------
def test_limit_reply_respects_group_cfg():
    long = "一二三四五六七八九十。后面全没了。"
    assert bot_mod._limit_reply({"max_reply_chars": 11}, long) == "一二三四五六七八九十。"
    assert bot_mod._limit_reply({"max_reply_chars": 0}, long) == long
    assert bot_mod._limit_reply({}, long) == long
    assert bot_mod._limit_reply({"max_reply_chars": "abc"}, long) == long
    assert bot_mod._limit_reply({"max_reply_chars": 5}, "") == ""


# ---------- webui 保存回读 ----------
YAML = """
app: {dry_run: true, web_port: 8790, db_file: "test.db"}
llm: {base_url: "http://x/v1", api_key: k1, model: m1, temperature: 0.8, timeout: 60}
kb: {url: "http://x/mcp", api_key: hex1, kb: team, top_k: 5, timeout: 60}
reply: {poll_sec: 3, quiesce_sec: 5, human_delay: [2, 5], history_limit: 16}
admins: [wxid_admin]
personas:
  通用朋友: {style: 口语化, background: ""}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    persona: 通用朋友
"""


class _StubBot:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = {"paused": False, "sent": 0, "start": time.time()}
        self.startup_error = None

    def build_watch(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg_path = tmp_path / "bot_config.yaml"
    cfg_path.write_text(YAML, encoding="utf-8")
    bot = _StubBot(BotConfig(str(cfg_path)), Store(str(tmp_path / "t.db")))

    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(webui, "threading", types.SimpleNamespace(Thread=_NoThread))
    app = webui.start_web(bot)
    app.testing = True
    return app.test_client(), str(cfg_path)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save_group(c, **extra):
    data = {"idx": "0", "do": "save", "enabled": "on",
            "display": "测试群", "id": "111@chatroom", "persona": "通用朋友",
            "qa_enabled": "on", "grounded": "on"}
    data.update(extra)
    return c.post("/cfg/group", data=data, follow_redirects=True)


def test_group_save_persists_max_reply_chars(client):
    c, path = client
    r = _save_group(c, max_reply_chars="60")
    assert r.status_code == 200
    cfg = _load(path)
    assert cfg["groups"][0]["max_reply_chars"] == 60


def test_group_save_zero_means_unlimited(client):
    c, path = client
    r = _save_group(c, max_reply_chars="0")
    assert r.status_code == 200
    cfg = _load(path)
    assert cfg["groups"][0]["max_reply_chars"] == 0


def test_group_save_keeps_old_limit_when_field_missing(client):
    # 手搓表单漏字段时，已有值不被冲掉（老键 carry-over 机制）
    c, path = client
    _save_group(c, max_reply_chars="80")
    r = _save_group(c)
    assert r.status_code == 200
    cfg = _load(path)
    assert cfg["groups"][0]["max_reply_chars"] == 80


def test_group_card_shows_limit_input(client):
    c, path = client
    _save_group(c, max_reply_chars="50")
    body = c.get("/").data.decode("utf-8")
    assert "max_reply_chars" in body
    assert 'value="50"' in body
