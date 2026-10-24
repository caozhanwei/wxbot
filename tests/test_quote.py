# -*- coding: utf-8 -*-
"""P1 引用消息解析与引用回复测试套件。"""
import pytest
from wxbot import driver, group_logic


SAMPLE_QUOTE_XML = """guyujie891205:
<?xml version="1.0"?>
<msg>
	<appmsg>
		<title>请问怎么可以联系上施工方？物业一直就是反应反应，拖着不来处理</title>
		<action>view</action>
		<type>57</type>
		<refermsg>
			<type>1</type>
			<svrid>2583615201141384391</svrid>
			<fromusr>53402327691@chatroom</fromusr>
			<chatusr>wxid_ae2md30wwi1c22</chatusr>
			<displayname>6-1-50x</displayname>
			<content>我自己去联系的开发商</content>
		</refermsg>
	</appmsg>
</msg>"""


def test_parse_quote_xml():
    """解析微信 57 类型引用消息 XML。"""
    res = driver.parse_refer_msg(SAMPLE_QUOTE_XML)
    assert res is not None
    assert res["is_quote"] is True
    assert res["reply"] == "请问怎么可以联系上施工方？物业一直就是反应反应，拖着不来处理"
    assert res["quoted_user"] == "6-1-50x"
    assert res["quoted_content"] == "我自己去联系的开发商"


def test_friendly_formats_quote():
    """_friendly 规整消息时将引用消息格式化为人类可读的引用前缀。"""
    class StubDriver(driver.WeChatDriver):
        def __init__(self):
            self.self_id = "me"
            self.self_name = "我的名字"
            self._media = None
            self._ocr_cache = {}
            self._voice_cache = {}
            from wxbot.ocr import OcrClient
            from wxbot.asr import AsrClient
            self.ocr = OcrClient(None)
            self.asr = AsrClient(None)

    d = StubDriver()
    m = {"content": SAMPLE_QUOTE_XML, "local_id": 123}
    text = d._friendly(m, user_id="53402327691@chatroom")
    assert "[引用 6-1-50x: \"我自己去联系的开发商\"]" in text
    assert "请问怎么可以联系上施工方" in text


def test_send_quote_fallback():
    """当无法调用原生引用时，降级使用前缀回复。"""
    class FakeUIA:
        def open_chat(self, who):
            self.chat = who
            return True
        def current_chat(self):
            return getattr(self, "chat", "测试群")
        def send_text(self, t):
            self.last_sent = t
            return True
        def quote_last_message(self, who, t, target_text=None, target_name=None):
            return False  # 模拟引用失败，应触发降级

    class StubDriver(driver.WeChatDriver):
        def __init__(self):
            self.self_id = "me"
            self.self_name = "我的名字"
            self.uia = FakeUIA()

    d = StubDriver()
    ok, why = d.send_quote("测试群", "没问题马上处理", target_name="张三", target_content="请问有人在吗")
    assert ok is True
    assert "回复 @张三" in d.uia.last_sent or "「引用」" in d.uia.last_sent
