"""会话名识别 + 剪贴板失败处理的离线自测（不碰微信、不写剪贴板）。

跑法：python tools/test_chat_identity.py
背景：2026-10-07 真机跑 OCR 关闭模式时炸出这两个问题——
  1) 输入框 Name 是「会话名+占位符尾巴」，`current_chat() == who` 全线失效；
  2) OpenClipboard 被别的进程占用时异常直接冒出 send_msg（traceback）。
"""
import os
import sys
import types

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

import pyperclip                                          # noqa: E402
from wechatauto import guia                               # noqa: E402
from wechatauto.guia import WeChatGUI                     # noqa: E402
from wechatauto.uia_driver import WeChatUIA, _clean_chat_name  # noqa: E402

guia.rhythm = types.SimpleNamespace(gate=lambda *a, **k: None,
                                    nap=lambda *a, **k: None,
                                    point=lambda b: (b[0], b[1]))
PASSED, FAILED = [], []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-46s got=%-14s want=%s" % ("ok" if ok else "FAIL", name, got, want))


print("--- A. 占位符尾巴剥离（本机 4.1.15.13 实测值）---")
check("理想答案：会话名+尾巴 → 会话名",
      _clean_chat_name("文件传输助手按住鼠标 语音输入文字"), "文件传输助手")
check("无空格变体", _clean_chat_name("同学群按住鼠标语音输入文字"), "同学群")
check("只剩尾巴词", _clean_chat_name("张三语音输入文字"), "张三")
check("没有尾巴 → 原样", _clean_chat_name("26级金高~4班（同学群）"), "26级金高~4班（同学群）")
check("普通会话名不误伤", _clean_chat_name("李小明"), "李小明")
check("空 Name → None", _clean_chat_name(None), None)
check("空白 Name → None", _clean_chat_name("   "), None)
check("会话名本身就叫这个也不吃掉（head 为空则原样)",
      _clean_chat_name("语音输入文字"), "语音输入文字")

# current_chat 走一遍真逻辑
eng = object.__new__(WeChatUIA)


class _Ctrl:
    def __init__(self, name):
        self.Name = name


eng._chat_input = lambda win=None: _Ctrl("文件传输助手按住鼠标 语音输入文字")
check("current_chat() 已剥尾巴", eng.current_chat(), "文件传输助手")
check("剥完就能 == 上", eng.current_chat() == "文件传输助手", True)
eng._chat_input = lambda win=None: None
check("控件不在 → None", eng.current_chat(), None)

print("\n--- B. 剪贴板被占：set_clipboard 返回 False 且不抛 ---")
real_copy = pyperclip.copy


def boom(text):
    raise RuntimeError("PyperclipWindowsException: Error calling OpenClipboard")


pyperclip.copy = boom
try:
    g = object.__new__(WeChatGUI)
    g.keys = []
    g._input = types.SimpleNamespace(key=lambda vk, ctrl=False: g.keys.append((vk, ctrl)),
                                     type_pinyin=lambda s: None,
                                     _user32=None)
    g._get_uia = lambda refresh=False: None
    g.get_input_box = lambda: (0, 0, 900, 200)
    g.focus_input = lambda box: True
    # 剪贴板写不进去 → 框里本不该有字，像素判定给 False（这才是真实形态）
    g._input_box_has_text = lambda box=None: False
    g._pending_text = None
    g._auto_recalibrated = True          # 别在测试里触发真重校准
    check("set_clipboard 返回 False", g.set_clipboard("你好"), False)
    check("完整路径不崩、返回 False", g.input_text("你好"), False)
    check("从没投递 Ctrl+V", (0x56, True) in g.keys, False)
    g2 = object.__new__(WeChatGUI)
    g2.keys = []
    g2._input = types.SimpleNamespace(key=lambda vk, ctrl=False: g2.keys.append((vk, ctrl)),
                                      type_pinyin=lambda s: None,
                                      _user32=None)
    g2._get_uia = lambda refresh=False: None
    g2.focus_input = lambda box: True
    g2._input_box_has_text = lambda box=None: True
    g2._pending_text = None
    check("fast 路径也返回 False",
          g2.input_text("你好", box=(0, 0, 900, 200), fast=True), False)
    check("fast 一次键都没投", g2.keys, [])
finally:
    pyperclip.copy = real_copy

print("\n--- C. 剪贴板正常时仍然成功（证明没把它改废）---")
seen = []
pyperclip.copy = lambda t: seen.append(t)
g = object.__new__(WeChatGUI)
g.keys = []
g._input = types.SimpleNamespace(key=lambda vk, ctrl=False: g.keys.append((vk, ctrl)),
                                 type_pinyin=lambda s: None, _user32=None)
g._get_uia = lambda refresh=False: None
g.get_input_box = lambda: (0, 0, 900, 200)
g.focus_input = lambda box: True
g._input_box_has_text = lambda box=None: True
g._pending_text = None
check("set_clipboard 成功", g.set_clipboard("你好"), True)
seen.clear()
check("input_text 成功", g.input_text("你好"), True)
check("剪贴板收到内容", seen, ["你好"])
check("投递了 Ctrl+V", (0x56, True) in g.keys, True)
check("待发文本已记录", g._pending_text, "你好")

print("\n结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
for n in FAILED:
    print("  失败:", n)
sys.exit(1 if FAILED else 0)
