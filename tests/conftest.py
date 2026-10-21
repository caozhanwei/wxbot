# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapter import MockWeChat  # noqa: E402
from db import DB  # noqa: E402


@pytest.fixture()
def config():
    return {
        "dry_run": True,
        "bot_nickname": "群助手",
        "rooms": ["room_1"],
        "admins": ["admin_wxid"],
        "wcf": {"rate_limit_seconds": 0, "jitter_seconds": [0, 0]},
        "mcp": {"url": "https://kb.test/mcp", "api_key": "k", "kb": "team", "top_k": 5,
                "timeout": 60, "miss_markers": ["未查询到", "无法回答"]},
        "llm": {"provider": "zhipu", "api_key": "", "model": "glm-4-flash"},
        "mining": {"reply_window_minutes": 30,
                   "thanks_words": ["谢谢", "感谢", "多谢", "thanks", "搞定"]},
        "welcome_text": "欢迎！@我提问",
        "forbidden_words": ["广告", "加微信互推"],
        "report": {"cron_hour": 20, "cron_minute": 0},
        "web": {"enabled": False, "port": 8777},
    }


@pytest.fixture()
def db(tmp_path):
    return DB(str(tmp_path / "test.db"))


@pytest.fixture()
def stack(config, db, tmp_path):
    """完整组件栈：MockWeChat + 假 RAGFlow + 假 LLM + router。"""
    from admin.commands import AdminCommands
    from gmgr import GroupManager
    from ingest import Ingestor
    from llm import LLM
    from mining.tracker import QuestionTracker
    from router import Router

    wechat = MockWeChat(config, db)
    llm = LLM("zhipu", "", "test-model")

    class FakeKB:
        def __init__(self):
            self.ready = True
            self.questions = []

        def ask(self, question, room=""):
            self.questions.append((room, question))
            if "NETLIST_FILE" in question:
                return "NETLIST_FILE 用于指定输出的网表文件路径。", True, True
            return "[未查询到相关内容]", False, False

    class FakeLLM(LLM):
        def __init__(self):
            super().__init__("zhipu", "fake-key", "fake")

        @property
        def ready(self):
            return True

        def chat_json(self, system, user, temperature=0.1):
            return {"q": "如何指定输出网表文件？", "a": "使用 NETLIST_FILE 命令指定输出路径。"}

    gmgr = GroupManager(config, db, wechat)
    router = Router(config, db, wechat, FakeKB(), None, None, gmgr)
    ingest = Ingestor(config, db, export_dir=tmp_path / "exports")
    tracker = QuestionTracker(config, db, FakeLLM())
    admin = AdminCommands(config, db, wechat, router, tracker, gmgr, ingest)
    router.tracker = tracker
    router.admin = admin

    wechat.start(router.dispatch)
    return {"wechat": wechat, "db": db, "router": router, "tracker": tracker,
            "admin": admin, "gmgr": gmgr, "ingest": ingest}
