# -*- coding: utf-8 -*-
"""引用回复按正文定位提问人那条消息：OCR 匹配 + driver 传参 + 定位失败降级不引错人。

背景：答疑计算要 10~40 秒，期间别人插话后"右键最新一行"就会引用到插话的人
（A 提问、引用到 B）。修复后按提问正文 OCR 定位；找不到就降级文本前缀，
绝不盲目引用最新一行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wechatauto.uia_driver import locate_quote_text
from wxbot import driver


# ---------- 纯匹配：locate_quote_text(ocr_items, target) ----------

def test_locate_exact_line():
    items = [("早上好", 10, 100, 60, 20),
             ("@群助手 今天几点开会", 10, 200, 160, 20),
             ("我也是想问", 10, 300, 80, 20)]
    pt = locate_quote_text(items, "@群助手 今天几点开会")
    assert pt == (10 + 160 // 2, 200 + 20 // 2)


def test_locate_prefers_bottom_most_same_quality():
    """引用气泡回显原话（上方）与真消息（下方）都命中：取最底部（最新）。"""
    items = [("张三: 今天几点开会", 10, 100, 150, 20),
             ("今天几点开会", 10, 260, 100, 20)]
    pt = locate_quote_text(items, "今天几点开会")
    assert pt[1] == 260 + 20 // 2


def test_locate_startswith_beats_lower_containment():
    """开头命中的真消息优先于更靠下但只是"包含"的回显。"""
    items = [("今天几点开会", 10, 100, 100, 20),
             ("楼上说今天几点开会来着", 10, 260, 160, 20)]
    pt = locate_quote_text(items, "今天几点开会")
    assert pt[1] == 100 + 20 // 2


def test_locate_wrapped_long_message():
    """长消息换行：前 12 字 probe 跨两行都不整段命中时，前缀 core 命中首行。"""
    items = [("人工智能到底是什么", 5, 150, 120, 20),
             ("技术，通俗讲讲", 5, 174, 100, 20)]
    pt = locate_quote_text(items, "人工智能到底是什么技术，通俗讲讲")
    assert pt is not None
    assert pt[1] == 150 + 20 // 2


def test_locate_tail_probe():
    """开头被 OCR 弄脏时，结尾 probe 兜底命中。"""
    items = [("●●请问这台设备怎么", 10, 120, 130, 20),
             ("进恢复模式", 10, 144, 80, 20)]
    pt = locate_quote_text(items, "请问这台设备怎么进恢复模式")
    assert pt is not None


def test_locate_whitespace_normalized():
    pt = locate_quote_text([("@群助手 在吗", 10, 50, 90, 20)], "@群助手   在吗")
    assert pt is not None


def test_locate_short_target_requires_startswith():
    """超短目标只允许开头精确命中，避免两三个字到处误中。"""
    assert locate_quote_text([("谁在吗", 10, 100, 60, 20)], "在吗") is None
    assert locate_quote_text([("在吗", 10, 100, 60, 20)], "在吗") is not None


def test_locate_none_when_absent():
    assert locate_quote_text([("随便聊聊", 1, 1, 2, 2)], "完全不同的话") is None
    assert locate_quote_text([], "任何") is None
    assert locate_quote_text([("随便", 1, 1, 2, 2)], "") is None


# ---------- driver 传参与降级 ----------

class _Stub(driver.WeChatDriver):
    def __init__(self, quote_ok=True):
        self.self_id = "me"
        self.self_name = "群助手"
        self.uia = _FakeUIA(quote_ok)


class _FakeUIA:
    def __init__(self, quote_ok):
        self.quote_ok = quote_ok
        self.calls = []
        self.chat = None
        self.last_sent = ""

    def open_chat(self, who):
        self.chat = who
        return True

    def current_chat(self):
        return self.chat

    def send_text(self, t):
        self.last_sent = t
        return True

    def quote_last_message(self, who, text, target_text=None):
        self.calls.append((who, text, target_text))
        return self.quote_ok


def test_driver_passes_target_content_to_uia():
    d = _Stub(quote_ok=True)
    ok, why = d.send_quote("测试群", "三点开会", target_name="张三",
                           target_content="@群助手 几点开会")
    assert ok is True
    assert d.uia.calls == [("测试群", "三点开会", "@群助手 几点开会")]


def test_driver_fallback_names_asker_not_others():
    """定位失败降级时，前缀必须点明提问人（宁缺勿错）。"""
    d = _Stub(quote_ok=False)
    ok, why = d.send_quote("测试群", "三点开会", target_name="张三",
                           target_content="@群助手 几点开会")
    assert ok is True
    assert "「回复 @张三」" in d.uia.last_sent
    assert "三点开会" in d.uia.last_sent
