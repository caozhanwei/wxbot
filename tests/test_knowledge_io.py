# -*- coding: utf-8 -*-
"""画像/记忆/问答知识资产的导出导入（跨账号迁移）。"""
import io
import json
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
app: {dry_run: true, web_port: 8791, db_file: "test.db"}
llm: {base_url: "http://x/v1", api_key: k1, model: m1, temperature: 0.8, timeout: 60}
reply: {poll_sec: 3, quiesce_sec: 5, human_delay: [2, 5], history_limit: 16, max_replies: 500}
admins: [wxid_admin]
personas:
  通用朋友: {style: 口语化, background: ""}
contacts:
  - {id: wxid_1, display: 张三, persona: 通用朋友, enabled: true}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
"""


# ---------- store 层 ----------

def _fill(store):
    store.upsert_profile("111@chatroom", "wxid_a", "阿明", "阿明的画像")
    store.upsert_contact_memory("wxid_1", "张三的记忆")
    store.add_mined("111@chatroom", [("问题1", "答案1")])
    store.add_qa_pending("测试群", "李四", "待沉淀问题", "带参考的答案")
    store.add_pending_issue("111@chatroom", "王五", "遗留问题", "上下文")


def test_export_roundtrip(tmp_path):
    a = Store(str(tmp_path / "a.db"))
    _fill(a)
    data = a.export_knowledge(source_wxid="wxid_src")
    assert data["wxbot_export"] == 2
    assert data["source_wxid"] == "wxid_src"
    assert data["member_profiles"][0]["nickname"] == "阿明"
    assert data["contact_memories"][0]["memory"] == "张三的记忆"
    assert data["mined_qa"][0]["question"] == "问题1"
    assert data["qa_pending"][0]["question"] == "待沉淀问题"
    assert data["pending_issues"][0]["question"] == "遗留问题"
    assert "config_yaml" not in data

    b = Store(str(tmp_path / "b.db"))
    counts = b.import_knowledge(data)
    assert counts == {"profiles": 1, "memories": 1, "mined_qa": 1,
                      "qa_pending": 1, "pending_issues": 1}
    assert b.get_profile("111@chatroom", "wxid_a")["profile"] == "阿明的画像"
    assert b.get_contact_memory("wxid_1") == "张三的记忆"
    assert b.mined_count() == 1
    assert b.qa_pending()[0]["question"] == "待沉淀问题"
    assert b.pending_issues()[0]["asker"] == "王五"


def test_export_with_config_yaml(tmp_path):
    a = Store(str(tmp_path / "a.db"))
    data = a.export_knowledge(config_yaml="app: {dry_run: true}\n")
    assert data["config_yaml"] == "app: {dry_run: true}\n"


def test_import_pending_sections_dedupe(tmp_path):
    src = Store(str(tmp_path / "s.db"))
    src.add_qa_pending("群", "李四", "问题A")
    src.add_pending_issue("群", "王五", "诉求B")
    dst = Store(str(tmp_path / "d.db"))
    data = src.export_knowledge()
    dst.import_knowledge(data)
    dst.import_knowledge(data)
    assert dst.import_knowledge(data)["qa_pending"] == 0
    assert len(dst.qa_pending(limit=100)) == 1
    assert len(dst.pending_issues(limit=100)) == 1


def test_import_conflict_skip_and_overwrite(tmp_path):
    src = Store(str(tmp_path / "s.db"))
    _fill(src)
    dst = Store(str(tmp_path / "d.db"))
    dst.upsert_profile("111@chatroom", "wxid_a", "阿明", "本地旧画像")

    counts = dst.import_knowledge(src.export_knowledge(), overwrite=False)
    assert counts["profiles"] == 0
    assert dst.get_profile("111@chatroom", "wxid_a")["profile"] == "本地旧画像"

    counts = dst.import_knowledge(src.export_knowledge(), overwrite=True)
    assert counts["profiles"] == 1
    assert dst.get_profile("111@chatroom", "wxid_a")["profile"] == "阿明的画像"


def test_import_mined_dedupe(tmp_path):
    src = Store(str(tmp_path / "s.db"))
    _fill(src)
    dst = Store(str(tmp_path / "d.db"))
    data = src.export_knowledge()
    dst.import_knowledge(data)
    dst.import_knowledge(data)
    assert dst.mined_count() == 1


def test_import_rejects_bad_payload(tmp_path):
    s = Store(str(tmp_path / "x.db"))
    assert s.import_knowledge({"member_profiles": "不是列表"}) == {
        "profiles": 0, "memories": 0, "mined_qa": 0,
        "qa_pending": 0, "pending_issues": 0}
    assert s.import_knowledge({"member_profiles": [
        {"nickname": "缺 wxid"}]})["profiles"] == 0


# ---------- webui 层 ----------

class StubBot:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = {"paused": False, "sent": 0, "start": time.time()}
        self.startup_error = None

    @property
    def driver(self):
        return None

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
    return app.test_client(), bot


def test_webui_export_download(client):
    c, bot = client
    bot.store.upsert_profile("111@chatroom", "wxid_a", "阿明", "画像内容")
    r = c.get("/knowledge/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    data = json.loads(r.data)
    assert data["member_profiles"][0]["profile"] == "画像内容"


def test_webui_import_upload(client):
    c, bot = client
    payload = {"wxbot_export": 1,
               "member_profiles": [{"grp": "111@chatroom", "wxid": "wxid_b",
                                    "nickname": "老王", "profile": "老王画像"}],
               "contact_memories": [{"wxid": "wxid_1", "memory": "记忆"}],
               "mined_qa": []}
    r = c.post("/knowledge/import",
               data={"file": (io.BytesIO(json.dumps(payload).encode("utf-8")),
                              "exp.json"), "overwrite": "1"},
               follow_redirects=True)
    assert r.status_code == 200
    assert bot.store.get_profile("111@chatroom", "wxid_b")["profile"] == "老王画像"
    assert bot.store.get_contact_memory("wxid_1") == "记忆"


def test_webui_import_bad_file(client):
    c, bot = client
    r = c.post("/knowledge/import",
               data={"file": (io.BytesIO(b"not json"), "exp.json")},
               follow_redirects=True)
    assert r.status_code == 200
    assert bot.store.profiles_count() == 0
