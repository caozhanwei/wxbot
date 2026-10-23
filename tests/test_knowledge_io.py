# -*- coding: utf-8 -*-
"""画像/记忆/问答知识资产的导出导入（跨账号迁移，ZIP 压缩包格式）。"""
import io
import json
import os
import sys
import time
import types
import zipfile

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
    assert r.mimetype == "application/zip"
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd and ".zip" in cd
    assert r.data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert "knowledge.json" in z.namelist()
        assert "bot_config.yaml" not in z.namelist()
        data = json.loads(z.read("knowledge.json").decode("utf-8"))
    assert data["member_profiles"][0]["profile"] == "画像内容"
    assert "config_yaml" not in data


def test_webui_export_with_cfg(client):
    c, bot = client
    r = c.get("/knowledge/export?cfg=1")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert "bot_config.yaml" in z.namelist()
        cfg_in_zip = z.read("bot_config.yaml").decode("utf-8")
        data = json.loads(z.read("knowledge.json").decode("utf-8"))
    assert cfg_in_zip == open(bot.cfg.path, encoding="utf-8").read()
    assert "config_yaml" not in data


def test_webui_import_upload(client):
    """旧版纯 JSON 文件仍然可导入（向后兼容）。"""
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


def _make_zip(payload, cfg_yaml=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("knowledge.json", json.dumps(payload, ensure_ascii=False))
        if cfg_yaml is not None:
            z.writestr("bot_config.yaml", cfg_yaml)
    return buf.getvalue()


def test_webui_import_zip(client):
    c, bot = client
    payload = {"wxbot_export": 2,
               "member_profiles": [{"grp": "111@chatroom", "wxid": "wxid_c",
                                    "nickname": "老李", "profile": "老李画像"}],
               "contact_memories": [], "mined_qa": []}
    r = c.post("/knowledge/import",
               data={"file": (io.BytesIO(_make_zip(payload)), "backup.zip")},
               follow_redirects=True)
    assert r.status_code == 200
    assert bot.store.get_profile("111@chatroom", "wxid_c")["profile"] == "老李画像"


def test_webui_import_zip_restore_cfg(client):
    c, bot = client
    payload = {"wxbot_export": 2, "member_profiles": [], "contact_memories": [],
               "mined_qa": []}
    old_cfg = open(bot.cfg.path, encoding="utf-8").read()
    r = c.post("/knowledge/import",
               data={"file": (io.BytesIO(_make_zip(payload, cfg_yaml="app: {dry_run: true}\n")),
                              "backup.zip"),
                     "restore_cfg": "1"},
               follow_redirects=True)
    assert r.status_code == 200
    assert open(bot.cfg.path, encoding="utf-8").read() == "app: {dry_run: true}\n"
    assert bot.cfg.data["app"]["dry_run"] is True
    # 还原现场，避免影响其它用例
    with open(bot.cfg.path, "w", encoding="utf-8") as fh:
        fh.write(old_cfg)
    bot.cfg.reload()


def test_webui_import_zip_without_cfg_entry(client):
    c, bot = client
    payload = {"wxbot_export": 2, "member_profiles": [], "contact_memories": [],
               "mined_qa": []}
    r = c.post("/knowledge/import",
               data={"file": (io.BytesIO(_make_zip(payload)), "backup.zip"),
                     "restore_cfg": "1"},
               follow_redirects=True)
    assert r.status_code == 200
    assert "配置未恢复" in r.data.decode("utf-8")


def test_webui_import_bad_file(client):
    c, bot = client
    r = c.post("/knowledge/import",
               data={"file": (io.BytesIO(b"not json"), "exp.json")},
               follow_redirects=True)
    assert r.status_code == 200
    assert bot.store.profiles_count() == 0
    assert "导入失败" in r.data.decode("utf-8")


def test_webui_import_zip_missing_knowledge(client):
    c, bot = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hello")
    r = c.post("/knowledge/import",
               data={"file": (io.BytesIO(buf.getvalue()), "backup.zip")},
               follow_redirects=True)
    assert r.status_code == 200
    assert bot.store.profiles_count() == 0
    assert "导入失败" in r.data.decode("utf-8")
