# -*- coding: utf-8 -*-
"""P2 历史消息全量冷启动导入测试套件。"""
import pytest
from wxbot import bootstrap, store


class FakeDriver:
    self_id = "me"
    self_name = "我的名字"
    def display_of(self, wxid):
        return f"Nick_{wxid}"
    class FakeDB:
        def get_messages(self, gid, limit=1000):
            return [
                {"sender_username": "user1", "content": "请问物业电话是多少？", "sort_seq": 1, "type": 1},
                {"sender_username": "user2", "content": "物业电话是 8888-6666", "sort_seq": 2, "type": 1},
                {"sender_username": "user1", "content": "收到，多谢邻居！", "sort_seq": 3, "type": 1},
                {"sender_username": "user3", "content": "大家家里暖气洞都怎么封的呀？下雨漏水好烦", "sort_seq": 4, "type": 1},
                {"sender_username": "user1", "content": "哈哈我也在愁这个", "sort_seq": 5, "type": 1},
            ]
    db = FakeDB()
    def member_lines(self, gid, wxid, limit=20):
        return ["发言1", "发言2", "发言3"]


class FakeLLM:
    def chat_json(self, system, user, temperature=0.2):
        if "画像" in system or "profile" in system:
            return {"profile": "热心邻居；关心暖气洞；住6栋"}
        if "抽取可长期沉淀" in system or "问答" in system:
            return [{"q": "物业电话是多少？", "a": "物业电话是 8888-6666"}]
        if "未解" in system or "待解决" in system:
            return [{"asker": "user3", "question": "暖气洞怎么封防漏水", "context": "下雨漏水好烦"}]
        return {}


class FakePersonas:
    llm = FakeLLM()


class FakeBot:
    def __init__(self, tmp_path):
        self.driver = FakeDriver()
        self.personas = FakePersonas()
        self.store = store.Store(str(tmp_path / "bot.db"))


def test_bootstrap_run(tmp_path):
    """测试全量冷启动运行：生成画像、挖掘问答、提取未解问题。"""
    bot = FakeBot(tmp_path)
    ok = bootstrap.run_bootstrap_sync(bot, "test@chatroom", "测试业主群", limit=100)
    assert ok is True
    # 验证画像写入
    profs = bot.store.profiles("test@chatroom")
    assert len(profs) >= 1
    assert "热心邻居" in profs[0]["profile"]
    # 验证问答沉淀
    pairs = bot.store.mined_pairs("test@chatroom")
    assert len(pairs) >= 1
    assert "8888-6666" in pairs[0]["a"]
    # 验证未解决问题
    pending = bot.store.pending_issues("test@chatroom")
    assert len(pending) >= 1
    assert "暖气洞" in pending[0]["question"]


def test_bootstrap_status():
    """测试状态查询接口。"""
    st = bootstrap.status()
    assert "running" in st
    assert "done_profiles" in st
    assert "done_qa" in st
    assert "done_pending" in st
