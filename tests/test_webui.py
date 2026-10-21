# -*- coding: utf-8 -*-
"""webui 可视化配置页：渲染 + 各保存端点落盘验证。"""
import os
import sys
import time
import types

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import webui
from wxbot.config import BotConfig
from wxbot.store import Store

YAML = """
app: {dry_run: true, web_port: 8790, db_file: "test.db"}
llm: {base_url: "http://x/v1", api_key: k1, model: m1, temperature: 0.8, timeout: 60}
kb: {url: "http://x/mcp", api_key: hex1, kb: team, top_k: 5, timeout: 60}
reply: {poll_sec: 3, quiesce_sec: 5, human_delay: [2, 5], history_limit: 16, max_replies: 500}
admins: [wxid_admin]
personas:
  通用朋友: {style: 口语化, background: ""}
  老友幽默: {style: 幽默, background: 老同事}
contacts:
  - {id: wxid_1, display: 张三, persona: 通用朋友, enabled: true}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    persona: 老友幽默
    qa: {enabled: true, grounded_reply: true, llm_fallback: true}
    manage:
      welcome: true
      welcome_text: "欢迎 {name} 入群"
      forbidden_words: [广告, 刷单]
      warn_persona: 老友幽默
      warn_threshold: 2
"""


class FakeDriver:
    """带群名反查的假 driver。"""

    def __init__(self):
        self.groups = {"9@chatroom": "🎈🏠测试小区群"}
        self.self_name = "测试号"

    def resolve_group_id(self, display):
        key = (display or "").strip().replace(" ", "")
        for u, n in self.groups.items():
            if key == n or key in n:
                return u, n
        return None


class StubBot:
    """模拟 Bot/BotShell 的最小外观（build_watch 可计数）。"""

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = {"paused": False, "sent": 0, "start": time.time()}
        self.startup_error = None
        self.rebuilt = 0
        self.fake_driver = FakeDriver()

    @property
    def driver(self):
        return self.fake_driver

    @property
    def personas(self):
        return None

    def build_watch(self):
        self.rebuilt += 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg_path = tmp_path / "bot_config.yaml"
    cfg_path.write_text(YAML, encoding="utf-8")
    bot = StubBot(BotConfig(str(cfg_path)), Store(str(tmp_path / "t.db")))

    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(webui, "threading", types.SimpleNamespace(Thread=_NoThread))
    app = webui.start_web(bot)
    app.testing = True
    return app.test_client(), bot, str(cfg_path)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_index_renders(client):
    c, bot, path = client
    r = c.get("/")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    for s in ("张三", "测试群", "通用朋友", "老友幽默", "演示模式", "广告, 刷单", "wxid_admin"):
        assert s in body, f"页面缺少 {s}"


def test_contact_new_save_del(client):
    c, bot, path = client
    r = c.post("/cfg/contact", data={"action": "new", "display": "李四",
                                     "id": "wxid_2", "persona": "通用朋友"},
               follow_redirects=True)
    assert r.status_code == 200
    data = _load(path)
    assert any(x["display"] == "李四" for x in data["contacts"])
    assert bot.rebuilt >= 1

    c.post("/cfg/contact", data={"idx": "1", "action": "save", "display": "李四四",
                                 "id": "wxid_2", "persona": "老友幽默", "enabled": "on"},
           follow_redirects=True)
    data = _load(path)
    assert data["contacts"][1]["display"] == "李四四"
    assert data["contacts"][1]["enabled"] is True

    c.post("/cfg/contact", data={"idx": "1", "action": "del"}, follow_redirects=True)
    assert len(_load(path)["contacts"]) == 1


def test_contact_new_requires_display(client):
    c, bot, path = client
    r = c.post("/cfg/contact", data={"action": "new", "display": ""})
    assert "昵称不能为空" in r.data.decode("utf-8")


def test_persona_rename_syncs_refs(client):
    c, bot, path = client
    c.post("/cfg/persona", data={"old_name": "通用朋友", "name": "新朋友",
                                 "style": "温和", "background": "", "do": "save"},
           follow_redirects=True)
    data = _load(path)
    assert "新朋友" in data["personas"] and "通用朋友" not in data["personas"]
    assert data["contacts"][0]["persona"] == "新朋友"


def test_admins_scope_keeps_dry_run(client):
    c, bot, path = client
    c.post("/cfg/app", data={"scope": "admins", "admins": "wxid_admin, wxid_b"},
           follow_redirects=True)
    data = _load(path)
    assert data["admins"] == ["wxid_admin", "wxid_b"]
    assert data["app"]["dry_run"] is True


def test_run_scope_updates(client):
    c, bot, path = client
    c.post("/cfg/app", data={"scope": "run", "dry_run": "on", "poll_sec": "7",
                             "quiesce_sec": "6", "hd_min": "1", "hd_max": "9",
                             "max_replies": "10"}, follow_redirects=True)
    data = _load(path)
    assert data["reply"]["poll_sec"] == 7
    assert data["reply"]["human_delay"] == [1, 9]
    assert data["app"]["dry_run"] is True
    c.post("/cfg/app", data={"scope": "run", "poll_sec": "7", "quiesce_sec": "6",
                             "hd_min": "1", "hd_max": "9", "max_replies": "10"},
           follow_redirects=True)
    assert _load(path)["app"]["dry_run"] is False


def test_group_save_roundtrip(client):
    c, bot, path = client
    c.post("/cfg/group", data={
        "idx": "0", "do": "save", "enabled": "on", "display": "测试群",
        "id": "111@chatroom", "persona": "老友幽默",
        "qa_enabled": "on", "grounded": "on", "llm_fallback": "on",
        "welcome": "on", "welcome_text": "欢迎 {name}",
        "forbidden": "广告, 刷单, 裸贷", "warn_persona": "老友幽默",
        "warn_threshold": "3",
    }, follow_redirects=True)
    g = _load(path)["groups"][0]
    assert g["manage"]["forbidden_words"] == ["广告", "刷单", "裸贷"]
    assert g["manage"]["warn_threshold"] == 3
    assert g["qa"]["llm_fallback"] is True


def test_group_new_and_del(client):
    c, bot, path = client
    c.post("/cfg/group", data={"idx": "-1", "do": "save", "enabled": "on",
                               "display": "新群", "id": "9@chatroom",
                               "persona": "通用朋友"}, follow_redirects=True)
    assert len(_load(path)["groups"]) == 2
    c.post("/cfg/group", data={"idx": "1", "do": "del"}, follow_redirects=True)
    assert len(_load(path)["groups"]) == 1


def test_group_auto_resolve_by_name(client):
    c, bot, path = client
    c.post("/cfg/group", data={"idx": "-1", "do": "save", "enabled": "on",
                               "display": "测试小区群", "id": "",
                               "persona": "通用朋友"}, follow_redirects=True)
    data = _load(path)
    assert len(data["groups"]) == 2
    assert data["groups"][1]["id"] == "9@chatroom"


def test_group_resolve_fail_shows_hint(client):
    c, bot, path = client
    r = c.post("/cfg/group", data={"idx": "-1", "do": "save", "enabled": "on",
                                   "display": "不存在的群", "id": "",
                                   "persona": "通用朋友"})
    body = r.data.decode("utf-8")
    assert "找不到" in body and "点开这个群聊" in body


def test_llm_and_kb_save(client):
    c, bot, path = client
    c.post("/cfg/llm", data={"base_url": "http://y/v1", "api_key": "k2",
                             "model": "m2", "temperature": "0.5", "timeout": "30"},
           follow_redirects=True)
    data = _load(path)
    assert data["llm"]["model"] == "m2" and data["llm"]["api_key"] == "k2"
    c.post("/cfg/kb", data={"url": "http://y/mcp", "api_key": "h2", "kb": "team2",
                            "top_k": "3", "timeout": "20"}, follow_redirects=True)
    data = _load(path)
    assert data["kb"]["kb"] == "team2" and data["kb"]["top_k"] == 3


def test_yaml_roundtrip(client):
    c, bot, path = client
    new_yaml = yaml.safe_dump({"app": {"dry_run": True},
                               "personas": {"x": {"style": "s"}}}, allow_unicode=True)
    r = c.post("/config", data={"cfg": new_yaml}, follow_redirects=True)
    assert r.status_code == 200
    assert _load(path)["personas"]["x"]["style"] == "s"
