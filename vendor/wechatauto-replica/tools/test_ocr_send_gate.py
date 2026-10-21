"""OCR/坐标发送路径的回读闸 + 前台收尾（置顶/遮挡窗口还原）的离线自测。

跑法：python tools/test_ocr_send_gate.py
不碰微信：WeChatGUI 用 object.__new__ 造壳，win32/输入/OCR 全换成记录用的桩。
"""
import os
import sys
import types

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

from wechatauto import guia                                  # noqa: E402
from wechatauto.guia import WeChatGUI                        # noqa: E402

guia.rhythm = types.SimpleNamespace(
    gate=lambda *a, **k: None, nap=lambda *a, **k: None,
    point=lambda box: (box[0], box[1]))

PASSED, FAILED = [], []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-46s got=%-10s want=%s" % ("ok" if ok else "FAIL", name, got, want))


class FakeInput:
    def __init__(self):
        self.keys = []
        self._user32 = None

    def key(self, vk, ctrl=False):
        self.keys.append((vk, ctrl))


class FakeU32:
    def __init__(self, iconic=(11, 22)):
        self.calls = []
        self.iconic = set(iconic)

    def ShowWindow(self, h, cmd):
        self.calls.append((h, cmd))
        return True

    def IsWindow(self, h):
        return True

    def IsIconic(self, h):
        return h in self.iconic

    def SetWindowPos(self, *a, **k):
        self.calls.append(("SetWindowPos", a[1] if len(a) > 1 else None))
        return True


class FakeUIA:
    def __init__(self, result=None, ctrl=True, raises=False):
        self.result = result
        self.ctrl = ctrl
        self.raises = raises
        self.reads = 0

    def _chat_input(self, win=None):
        return None if not self.ctrl else object()

    def _paste_landed(self, ctrl, text):
        self.reads += 1
        if self.raises:
            raise RuntimeError("探测炸了")
        return self.result if self.result else (True, "", 1.0)


def make_gui(uia=None, seq=(True, False), iconic=(11, 22)):
    """seq 是「输入框里有字」的真值序列：回车前 True、发出去后 False。"""
    g = object.__new__(WeChatGUI)
    g._input = FakeInput()
    g._u32 = FakeU32(iconic=iconic)
    g._input._user32 = g._u32
    g.main_hwnd = 67092
    g.origin_x = g.origin_y = 0
    g.send_button_region = (0, 0, 1, 1)
    g._last_input_box = (0, 0, 900, 200)
    g._pending_text = None
    g._blocked_minimized = []
    g._seq = list(seq)
    g._get_uia = lambda refresh=False: uia
    g._input_box_has_text = lambda box=None: (g._seq.pop(0) if g._seq else False)
    g.get_input_box = lambda: g._last_input_box
    g.ocr = lambda region: []
    g.wx_click = lambda *a: None
    g.focus_input = lambda box: True
    g.set_clipboard = lambda text: None
    g._update_render_rect = lambda: None
    g.is_alive = lambda: True
    return g


TXT = "今天下午三点开会"

print("--- A. _pending_text_ok 的放行/拦截分支 ---")
check("没有待发文本 → 放行", make_gui(FakeUIA((False, "", 0.0)))._pending_text_ok(None), True)
check("UIA 不可用 → 放行", make_gui(None)._pending_text_ok(TXT), True)
check("输入框定位不到 → 放行", make_gui(FakeUIA(ctrl=False))._pending_text_ok(TXT), True)
check("探测抛异常 → 放行", make_gui(FakeUIA(raises=True))._pending_text_ok(TXT), True)
check("比对达标 → 放行", make_gui(FakeUIA((True, TXT, 1.0)))._pending_text_ok(TXT), True)
check("比对不达标 → 拦", make_gui(FakeUIA((False, "", 0.0)))._pending_text_ok(TXT), False)

print("\n--- B. click_send：内容不符时绝不回车 ---")
uia = FakeUIA((False, "", 0.0))
g = make_gui(uia)
g._pending_text = TXT
check("粘贴落空 → 返回 False", g.click_send(), False)
check("确实回读了一次", uia.reads, 1)
check("一个键都没投递", g._input.keys, [])

uia2 = FakeUIA((True, TXT, 1.0))
g = make_gui(uia2, seq=(True, False))
g._pending_text = TXT
check("内容相符 → 返回 True", g.click_send(), True)
check("回了车", any(vk == guia.VK_RETURN for vk, _ in g._input.keys), True)
check("待发文本被一次性消费", g._pending_text, None)
g = make_gui(FakeUIA((False, "", 0.0)), seq=(True, False))   # 闸说不过，但没有待发文本
check("陈旧值不会拦住下一次发送", g.click_send(), True)

print("\n--- C. input_text 是否把文本交给闸 ---")
g = make_gui(FakeUIA((True, TXT, 1.0)), seq=(True,))
check("完整路径 input_text 成功", g.input_text(TXT), True)
check("记录了待发文本", g._pending_text, TXT)
g = make_gui(FakeUIA((True, TXT, 1.0)), seq=(True,))
check("fast 路径 input_text 成功", g.input_text(TXT, box=(0, 0, 900, 200), fast=True), True)
check("fast 也记录了", g._pending_text, TXT)

print("\n--- D. 置顶与遮挡窗口的收尾 ---")
seen = []
g = make_gui()
g.bring_to_front = lambda keep_topmost=False: (seen.append(keep_topmost), True)[1]
g._minimize_blockers = lambda: 0
g._last_visible_ok = False
g.ensure_visible()
check("ensure_visible 默认仍保持置顶（批量行为不变）", seen, [True])
seen.clear()
g._last_visible_ok = False          # 绕开 15 秒复用缓存，否则第二次根本不调用
g.ensure_visible(keep_topmost=False)
check("可显式要求不保持置顶", seen, [False])

g = make_gui(iconic=(11, 22))
g._blocked_minimized = [11, 22]     # 工厂里被重置成空表，这里按「本对象压下去的」填回
check("还原 2 个遮挡窗口", g.restore_blockers(), 2)
check("用 SW_SHOWNOACTIVATE(4)，还原不抢前台",
      [c for c in g._u32.calls if c[0] != "SetWindowPos"], [(11, 4), (22, 4)])
check("还原后记账清空", g._blocked_minimized, [])
check("再调一次不重复还原", g.restore_blockers(), 0)

g = make_gui(iconic=(11,))
g._blocked_minimized = [11, 22]
g.restore_zorder = lambda: g._u32.calls.append(("SetWindowPos", -2))
check("release_foreground 只还原仍最小化的那个", g.release_foreground(), 1)
check("同时取消了置顶", ("SetWindowPos", -2) in g._u32.calls, True)
check("已恢复的不再记账", g._blocked_minimized, [22])

print("\n结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
for nm in FAILED:
    print("  失败:", nm)
sys.exit(1 if FAILED else 0)
