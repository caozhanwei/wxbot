# -*- coding: utf-8 -*-
"""P4 语音识别多模态 ASR 测试套件。"""
import pytest
from wxbot import asr, driver


def test_asr_client_unconfigured():
    """未配置 ASR 时 ready 为 False，transcribe 返回 None。"""
    c = asr.AsrClient(None)
    assert c.ready is False
    assert c.transcribe(b"dummy_silk_data") is None


def test_asr_client_mock_success(monkeypatch):
    """配置可用时成功返回语音转写文本。"""
    c = asr.AsrClient({"url": "http://mock-asr/v1/audio/transcriptions", "api_key": "test_key"})
    assert c.ready is True

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"text": "明天上午开会，请大家准时到场"}
        text = '{"text": "明天上午开会，请大家准时到场"}'

    monkeypatch.setattr(asr.requests, "post", lambda *a, **kw: FakeResponse())
    text = c.transcribe(b"fake_audio_stream")
    assert text == "明天上午开会，请大家准时到场"


def test_friendly_renders_voice():
    """_friendly 处理语音消息时调用 voice_text 并格式化。"""
    class StubDriver(driver.WeChatDriver):
        def __init__(self):
            self.self_id = "me"
            self.self_name = "我的名字"
            self._media = None
            self._ocr_cache = {}
            self._voice_cache = {}
            from wxbot.ocr import OcrClient
            self.ocr = OcrClient(None)
            self.asr = asr.AsrClient(None)
        def voice_text(self, user_id, local_id):
            return "好嘞晚上一起吃饭"

    d = StubDriver()
    m = {"type": "voice", "local_id": 999, "content": "[voice]"}
    rendered = d._friendly(m, user_id="friend1")
    assert rendered == "[语音:好嘞晚上一起吃饭]"
