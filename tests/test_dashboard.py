# -*- coding: utf-8 -*-
"""R1 驾驶舱：今日群统计卡片 + 主动动作审计 + 日报配置表单 + 群级日报开关。"""
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

from test_webui import YAML, FakeDriver, StubBot   # 复用同一套 fake


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


def test_dashboard_card_shows_today_stats(client):
    c, bot, path = client
    day = time.strftime("%Y-%m-%d")
    bot.store.upsert_daily_stats(day, "111@chatroom", msg_count=120, active_members=8,
                                 top_words="停水 物业 维修", pending_count=3)
    body = c.get("/").data.decode("utf-8")
    for s in ("运营驾驶舱", "测试群", "120", "停水 物业 维修"):
        assert s in body, f"驾驶舱缺少 {s}"


def test_dashboard_digest_status_and_gate_audit(client):
    c, bot, path = client
    bot.store.log("gate", "文件传输助手", "digest", "【测试群】今日热点：停水")
    body = c.get("/").data.decode("utf-8")
    assert "动作审计" in body and "文件传输助手" in body
    assert "未开启" in body                     # 群未配 digest → 状态如实显示


def test_group_digest_checkbox_persists(client):
    c, bot, path = client
    base = {"idx": "0", "do": "save", "display": "测试群", "id": "111@chatroom",
            "enabled": "on", "persona": "老友幽默", "qa_enabled": "on", "grounded": "on"}
    c.post("/cfg/group", data=dict(base, digest_enabled="on", digest_to_group="on"),
           follow_redirects=True)
    data = _load(path)
    assert data["groups"][0]["digest"]["enabled"] is True
    assert data["groups"][0]["digest"]["to_group"] is True
    # 再存一次不带勾选 → 关闭（幂等替换，不残留）
    c.post("/cfg/group", data=base, follow_redirects=True)
    data = _load(path)
    assert data["groups"][0]["digest"]["enabled"] is False
    assert data["groups"][0]["digest"]["to_group"] is False


def test_daily_digest_form_saves(client):
    c, bot, path = client
    c.post("/cfg/app", data={"scope": "digest", "dd_enabled": "on",
                             "dd_hour": "20", "dd_push_to": "文件传输助手"},
           follow_redirects=True)
    dd = _load(path)["app"]["daily_digest"]
    assert dd["enabled"] is True and dd["hour"] == 20
    assert dd["push_to"] == "文件传输助手"
    # 非法小时回落 21
    c.post("/cfg/app", data={"scope": "digest", "dd_hour": "abc"},
           follow_redirects=True)
    assert _load(path)["app"]["daily_digest"]["hour"] == 21
