# -*- coding: utf-8 -*-
"""webui 关于页：版本展示 / 检查更新 / 下载 / 一键升级路由。自包含 fixture。"""
import os
import sys
import time
import types

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import updater, webui
from wxbot.config import BotConfig
from wxbot.store import Store

YAML = """
app: {dry_run: true, web_port: 8791, db_file: "test.db"}
llm: {base_url: "http://x/v1", api_key: k1, model: m1, temperature: 0.8, timeout: 60}
admins: [wxid_admin]
personas:
  通用朋友: {style: 口语化, background: ""}
contacts:
  - {id: wxid_1, display: 张三, persona: 通用朋友, enabled: true}
groups: []
"""


class FakeDriver:
    self_name = "测试号"
    self_id = "wxid_self"

    def resolve_id(self, display):
        return ""

    def resolve_group_id(self, display):
        return None

    def list_recent_chats(self, limit=60):
        return []

    def search_chats(self, q, limit=12):
        return []

    def mention_names(self, g):
        return [self.self_name]


class StubBot:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = {"paused": False, "sent": 0, "start": time.time()}
        self.startup_error = None
        self.fake_driver = FakeDriver()

    @property
    def driver(self):
        return self.fake_driver

    @property
    def personas(self):
        return None

    def build_watch(self):
        pass


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
    yield app.test_client(), bot
    updater._STATUS.update(state="idle", progress=0, total=0, msi="", latest="", error="")
    webui._UPDATE_CHECK.clear()


def _fake_check(newer=True, ok=True, msi_url="https://x/wxbot-v9.9.9.msi"):
    def _c(*a, **k):
        if not ok:
            return {"ok": False, "latest": "", "newer": False, "msi_url": "", "notes": "",
                    "message": "连接超时"}
        return {"ok": True, "latest": "v9.9.9" if newer else "v1.0.0",
                "newer": newer, "msi_url": msi_url, "notes": "修复了若干问题", "message": ""}
    return _c


def test_about_card_shows_version(client):
    c, bot, = client
    body = c.get("/").data.decode("utf-8")
    assert "关于与更新" in body
    assert "检查更新" in body
    assert f"v{updater.current_version().lstrip('v')}" in body


def test_check_update_newer_shows_download(client, monkeypatch):
    c, bot = client
    monkeypatch.setattr(webui.updater, "check_latest", _fake_check(newer=True))
    c.post("/update/check")
    body = c.get("/").data.decode("utf-8")
    assert "v9.9.9" in body and "下载新版本" in body


def test_check_update_latest_no_button(client, monkeypatch):
    c, bot = client
    monkeypatch.setattr(webui.updater, "check_latest", _fake_check(newer=False))
    c.post("/update/check")
    body = c.get("/").data.decode("utf-8")
    assert "已是最新版本" in body and "下载新版本" not in body


def test_check_update_error_shown(client, monkeypatch):
    c, bot = client
    monkeypatch.setattr(webui.updater, "check_latest", _fake_check(ok=False))
    c.post("/update/check")
    body = c.get("/").data.decode("utf-8")
    assert "连接超时" in body


def test_download_requires_check_first(client, monkeypatch):
    c, bot = client
    r = c.post("/update/download", follow_redirects=True)
    assert "请先点" in r.data.decode("utf-8")


def test_download_starts_and_progress_then_apply(client, monkeypatch, tmp_path):
    c, bot = client
    monkeypatch.setattr(webui.updater, "check_latest", _fake_check(newer=True))
    c.post("/update/check")

    started = {}

    def fake_dl(url, latest):
        started["url"] = url
        updater._STATUS.update(state="ready", msi=str(tmp_path / "wxbot-v9.9.9.msi"),
                               latest=latest, progress=1, total=1, error="")
        return True

    monkeypatch.setattr(webui.updater, "download_async", fake_dl)
    r = c.post("/update/download", follow_redirects=True)
    assert started["url"].endswith(".msi")
    body = c.get("/").data.decode("utf-8")
    assert "立即安装并重启" in body

    applied = {}
    monkeypatch.setattr(webui.updater, "apply_update",
                        lambda msi: applied.update(msi=msi) or str(tmp_path / "apply.ps1"))
    r = c.post("/update/apply")
    assert applied["msi"].endswith("wxbot-v9.9.9.msi")
    assert "安装" in r.data.decode("utf-8")


def test_apply_requires_ready(client, monkeypatch):
    c, bot = client
    updater._STATUS.update(state="idle")
    r = c.post("/update/apply", follow_redirects=True)
    assert "还没下载完成" in r.data.decode("utf-8")
