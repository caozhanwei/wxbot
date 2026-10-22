# -*- coding: utf-8 -*-
"""P3 私聊与群聊去 AI 味拟人化测试套件。"""
import pytest
from wxbot import personas, store


def test_split_reply_bubbles_short():
    """短消息不强拆，保持单气泡。"""
    bubbles = personas.split_reply_bubbles("行啊，那明天上午十点见")
    assert len(bubbles) == 1
    assert bubbles[0] == "行啊，那明天上午十点见"


def test_split_reply_bubbles_multi():
    """较长或有自然停顿的消息拆成 2~3 个短气泡。"""
    text = "刚在忙没看手机。那个事情我问过老王了，下周一能批下来！放心哈 [愉快]"
    bubbles = personas.split_reply_bubbles(text)
    assert 2 <= len(bubbles) <= 3
    assert "刚在忙" in bubbles[0]
    assert "批下来" in bubbles[1]


def test_human_delay_calculation():
    """根据字数拟真计算延迟时间：短消息快，长消息稍慢，在合理区间内。"""
    d_short = personas.human_delay_for_text("好的")
    d_long = personas.human_delay_for_text("这个文档我看了一下，核心在第三章的接口定义，建议你先按照那个规范修改然后再发我 review 一次。")
    assert 1.0 <= d_short <= 4.0
    assert d_long > d_short
    assert d_long <= 12.0


def test_clean_ai_markers():
    """消除 AI 口头禅、公文腔及句尾句号。"""
    raw = "您好！关于您咨询的问题，首先请检查网络，其次重启服务。希望对您有所帮助。"
    cleaned = personas.clean_ai_markers(raw)
    assert not cleaned.startswith("您好")
    assert "希望对您有所帮助" not in cleaned
    assert not cleaned.endswith("。")


def test_contact_memories_store(tmp_path):
    """联系人私聊记忆持久化。"""
    s = store.Store(str(tmp_path / "test.db"))
    assert s.get_contact_memory("wxid_friend") == ""
    s.upsert_contact_memory("wxid_friend", "喜欢钓鱼；有两个孩子；最近换了电车")
    mem = s.get_contact_memory("wxid_friend")
    assert "钓鱼" in mem
    assert "换了电车" in mem


def test_casual_group_reply():
    """群聊闲聊玩笑时，使用人设口吻打趣，绝不使用死模板。"""
    class FakeLLM:
        def chat(self, system, user, temperature=0.8):
            return "哈哈大佬又调侃我，刚在忙呢 [呲牙]"

    class FakeConfig:
        llm_cfg = {"temperature": 0.8}
        def persona(self, name):
            return {"style": "幽默风趣、接地气", "background": "老朋友"}

    p = personas.PersonaEngine(FakeConfig())
    p.llm = FakeLLM()
    reply = p.casual_group_reply("老友幽默", "老司机养成记", ["张三: 出来冒泡"], "张三", "出彩了吗")
    assert "哈哈我在呢，有问题随时@我" not in reply
    assert len(reply) > 0
