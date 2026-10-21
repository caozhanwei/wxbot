# -*- coding: utf-8 -*-
"""深度理解三件套：图片 OCR（含会话/上传分流）、@提问理解（standalone）、群友画像。"""
import json
import os
import sys
import time
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import LLM, LLMError
from wxbot import profiles
from wxbot.driver import WxDriver
from wxbot.ocr import OcrClient, OcrError
from wxbot.personas import PersonaEngine
from wxbot.store import Store


# ---------- OCR 客户端 ----------

class _Resp:
    def __init__(self, status=200, text="", headers=None, json_data=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}
        self._json = json_data
        self.encoding = None

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _sse(payload):
    return "event: message\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _ok_result(text):
    return _Resp(text=_sse({"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text",
                     "text": json.dumps({"full_text": text}, ensure_ascii=False)}]}}))


def _init_resp():
    return _Resp(text="ok", headers={"Mcp-Session-Id": "sid-1"})


def test_ocr_not_ready_returns_empty():
    assert OcrClient(None).ocr(b"x") == ""
    assert OcrClient({}).ocr(b"x") == ""


def test_ocr_small_image_direct_call():
    c = OcrClient({"url": "http://o:1", "api_key": "k", "timeout": 5})
    with mock.patch("wxbot.ocr.requests.post") as post:
        post.side_effect = [_init_resp(), _ok_result("通知：明天 9 点停水")]
        out = c.ocr(b"tiny")
    assert out == "通知：明天 9 点停水"
    assert post.call_count == 2
    init, call = post.call_args_list
    assert init.args[0] == "http://o:1/mcp"
    assert call.kwargs["headers"]["Mcp-Session-Id"] == "sid-1"
    args = call.kwargs["json"]["params"]
    assert args["name"] == "ocr_image_base64"
    assert len(args["arguments"]["image_base64"]) <= 25000


def test_ocr_big_image_uploads_first():
    c = OcrClient({"url": "http://o:1", "api_key": "k"})
    big = b"x" * 20000                    # base64 后 > 25000，走 /upload
    with mock.patch("wxbot.ocr.requests.post") as post:
        post.side_effect = [
            _Resp(json_data={"server_path": "/srv/1.jpg"}),   # /upload（先于 initialize）
            _init_resp(),
            _ok_result("价格表：滤芯 59 元")]
        out = c.ocr(big)
    assert out == "价格表：滤芯 59 元"
    up = post.call_args_list[0]
    assert up.args[0] == "http://o:1/upload"
    assert up.kwargs["files"]["file"][0] == "img.jpg"
    args = post.call_args_list[2].kwargs["json"]["params"]
    assert args["name"] == "ocr_image" and args["arguments"]["image_path"] == "/srv/1.jpg"


def test_ocr_session_expired_reinitializes():
    c = OcrClient({"url": "http://o:1", "api_key": "k"})
    bad = _Resp(status=404, text="Session not found")
    with mock.patch("wxbot.ocr.requests.post") as post:
        post.side_effect = [_init_resp(), bad, _init_resp(), _ok_result("重试成功")]
        assert c.ocr(b"tiny") == "重试成功"
    assert post.call_count == 4


def test_ocr_http_error_raises():
    c = OcrClient({"url": "http://o:1", "api_key": "k"})
    with mock.patch("wxbot.ocr.requests.post") as post:
        post.side_effect = [_Resp(status=500, text="boom")]
        with pytest.raises(OcrError):
            c.ocr(b"tiny")


# ---------- LLM 空内容重试 ----------

def test_llm_empty_content_retries_then_ok():
    llm = LLM("custom", "k", "m", base_url="http://x/v1")
    empty = _Resp(json_data={"choices": [{"message": {"content": ""}}]})
    good = _Resp(json_data={"choices": [{"message": {"content": "答案"}}]})
    with mock.patch("llm.requests.post") as post:
        post.side_effect = [empty, good]
        assert llm.chat("s", "u") == "答案"
    assert post.call_count == 2
    assert post.call_args.kwargs["json"]["max_tokens"] == 2000


def test_llm_empty_content_gives_up():
    llm = LLM("custom", "k", "m", base_url="http://x/v1")
    empty = _Resp(json_data={"choices": [{"message": {"content": ""}}]})
    with mock.patch("llm.requests.post") as post:
        post.side_effect = [empty, empty]
        with pytest.raises(LLMError):
            llm.chat("s", "u")


# ---------- @提问理解 ----------

def test_understand_question_fills_standalone():
    seen = {}

    class FL:
        def chat_json(self, system, user, temperature=0.1):
            seen["system"], seen["user"] = system, user
            return {"standalone": "群里讨论的净水器滤芯多少钱", "should_answer": True}

    e = PersonaEngine(None)
    e._llm = FL()
    u = e.understand_question("业主群", ["张三: 这个滤芯能用多久", "李四: 它多少钱"],
                              "它多少钱", "李四", profile="住6栋，关心暖气")
    assert u == {"standalone": "群里讨论的净水器滤芯多少钱", "should_answer": True}
    assert "独立问题" in seen["system"] and "它多少钱" in seen["user"]
    assert "住6栋" in seen["user"]


def test_understand_question_defaults_on_missing_fields():
    class FL:
        def chat_json(self, system, user, temperature=0.1):
            return {}

    e = PersonaEngine(None)
    e._llm = FL()
    u = e.understand_question("g", [], "原问题", "张三")
    assert u["standalone"] == "原问题" and u["should_answer"] is True


# ---------- 群友画像 ----------

class _ProfilesDriver:
    self_id = "wxid_bot"

    def member_lines(self, grp, wxid, limit=20):
        return {("g1", "wxid_a"): ["暖气什么时候来", "物业电话是多少"]}.get((grp, wxid), [])

    def display_of(self, uid):
        return {"wxid_a": "张三", "wxid_b": "李四", "wxid_bot": "我"}.get(uid, uid)


class _ProfilesDB:
    def get_messages(self, gid, limit=200, offset=0):
        return [{"sender_username": "", "content": "wxid_a: 你好"},
                {"sender_username": "", "content": "wxid_b: 哈哈"},
                {"sender_username": "wxid_bot", "content": "我发的"},
                {"sender_username": "", "content": "广告勿理"}]


class _FL:
    def __init__(self, text="住6栋；关心暖气和物业；语气直接"):
        self.text = text
        self.n = 0

    def chat_json(self, system, user, temperature=0.1):
        self.n += 1
        return {"profile": self.text}


class _P:
    def __init__(self, llm):
        self.llm = llm


def test_update_profile_and_throttle(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    llm = _FL()
    drv = _ProfilesDriver()
    p1 = profiles.update_profile(_P(llm), store, drv, "g1", "wxid_a", "张三")
    assert "暖气" in p1 and llm.n == 1
    row = store.get_profile("g1", "wxid_a")
    assert row["nickname"] == "张三" and "住6栋" in row["profile"]
    # 1 小时内节流：不再调 LLM，直接回旧画像
    p2 = profiles.update_profile(_P(llm), store, drv, "g1", "wxid_a", "张三")
    assert p2 == p1 and llm.n == 1
    # 过期后允许重算
    store.upsert_profile("g1", "wxid_a", "张三", "旧画像")
    with mock.patch("wxbot.profiles.time.time", return_value=time.time() + 7200):
        p3 = profiles.update_profile(_P(llm), store, drv, "g1", "wxid_a", "张三")
    assert llm.n == 2 and "暖气" in p3


def test_update_profile_failure_keeps_old(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.upsert_profile("g1", "wxid_a", "张三", "旧画像")

    class Boom:
        def chat_json(self, s, u, temperature=0.1):
            raise RuntimeError("llm down")

    out = profiles.update_profile(_P(Boom()), store, _ProfilesDriver(), "g1", "wxid_a", "张三")
    assert out == "旧画像"


def test_active_members_excludes_self_and_ad():
    drv = _ProfilesDriver()
    drv.db = _ProfilesDB()
    ids = profiles.active_members(drv, "g1@chatroom")
    assert ids == ["wxid_a", "wxid_b"]


def test_store_profiles_roundtrip(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.upsert_profile("g1", "a", "甲", "画像甲")
    store.upsert_profile("g2", "b", "乙", "画像乙")
    rows = store.profiles()
    assert len(rows) == 2
    assert store.profiles(grp="g1")[0]["nickname"] == "甲"


# ---------- driver 集成：local_id / 图片上下文 / member_lines ----------

class _DriverDB:
    def __init__(self, rows):
        self.rows = rows

    def get_messages(self, uid, limit=32, offset=0):
        return list(self.rows)[:limit]

    def get_new_messages(self, uid, since):
        return [r for r in self.rows if r["sort_seq"] > since]

    def get_nickname(self, uid):
        return {"wxid_a": "阿强"}.get(uid, uid)


class _FakeOcr:
    ready = True

    def ocr(self, data):
        return "通知：明天停水"


def _bare_driver(rows, ocr=None):
    d = object.__new__(WxDriver)
    d.db = _DriverDB(rows)
    d.self_id = "wxid_bot"
    d.self_name = "群助手"
    d.ocr = ocr or OcrClient(None)
    d._media = None
    d._ocr_cache = {}
    return d


_IMG_ROW = {"sort_seq": 2, "type": "图片", "create_time": 2,
            "sender_username": "wxid_a", "local_id": 77,
            "content": '<?xml><msg><img src="a.dat"/></msg>'}

_ROWS = [
    {"sort_seq": 1, "type": "文本", "create_time": 1, "local_id": 1,
     "sender_username": "wxid_a", "content": "wxid_a:\n大家看这张通知"},
    _IMG_ROW,
    {"sort_seq": 3, "type": "文本", "create_time": 3, "local_id": 3,
     "sender_username": "", "content": "wxid_a:\n收到"},
]


def test_poll_carries_local_id():
    d = _bare_driver(_ROWS)
    msgs, since = d.poll("g@chatroom", 0)
    assert since == 3
    img = next(m for m in msgs if m["type"] == "图片")
    assert img["local_id"] == 77


def test_friendly_image_uses_ocr_cache():
    d = _bare_driver(_ROWS, ocr=_FakeOcr())
    d._ocr_cache[("g@chatroom", 77)] = "通知：明天停水"
    assert d._friendly(_IMG_ROW, "g@chatroom") == "[图片:通知：明天停水]"
    # 无缓存且 OCR 未就绪 → 解密必然失败，降级为 [图片] 不抛错
    d2 = _bare_driver(_ROWS)
    assert d2._friendly(_IMG_ROW, "g@chatroom") == "[图片]"


def test_build_context_renders_image_text():
    d = _bare_driver(_ROWS, ocr=_FakeOcr())
    d._ocr_cache[("g@chatroom", 77)] = "通知：明天停水"
    lines = d.build_context("g@chatroom", limit=10)
    assert any("[图片:通知：明天停水]" in l for l in lines)
    assert any(l.startswith("阿强: ") for l in lines)


def test_member_lines_filters_by_sender():
    d = _bare_driver(_ROWS)
    # 新到旧；图片 OCR 未就绪时降级为 [图片] 占位（仍算该成员的发言）
    assert d.member_lines("g@chatroom", "wxid_a") == ["收到", "[图片]", "大家看这张通知"]
    assert d.member_lines("g@chatroom", "wxid_b") == []
