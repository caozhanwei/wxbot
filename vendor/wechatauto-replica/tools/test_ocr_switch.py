"""WxParam.ENABLE_OCR 总闸的离线自测（不截图、不调 OCR 引擎）。

跑法：python tools/test_ocr_switch.py
"""
import inspect
import os
import sys
import types

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

from wechatauto import guia                                   # noqa: E402
from wechatauto.guia import ScreenOCR, WeChatGUI              # noqa: E402
from wechatauto.param import WxParam                          # noqa: E402

guia.rhythm = types.SimpleNamespace(gate=lambda *a, **k: None,
                                    nap=lambda *a, **k: None,
                                    point=lambda b: (b[0], b[1]))
PASSED, FAILED = [], []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-46s got=%-9s want=%s" % ("ok" if ok else "FAIL", name, got, want))


class BoomImage:
    """任何真实 OCR 路径都会对它调 save/size —— 一碰就炸，用来证明没碰。"""

    def save(self, *a, **k):
        raise AssertionError("不该走到 OCR：image.save 被调用了")

    @property
    def size(self):
        raise AssertionError("不该走到 OCR：image.size 被读了")


class FakeImg:
    """给放大档用的假图：只支持 size/resize，不碰真实 OCR。"""

    def __init__(self, w=10, h=5):
        self.size = (w, h)

    def resize(self, wh, *_a):
        return FakeImg(*wh)


class FakeUIA:
    def __init__(self, cur=None, raises=False):
        self.cur, self.raises = cur, raises

    def current_chat(self):
        if self.raises:
            raise RuntimeError("UIA 炸了")
        return self.cur


def make_gui(uia=None, ocr_rows=()):
    """造壳：只桩掉截图/UIA/节奏，ocr() 与 ocr_zoomed() 用生产代码本身。"""
    g = object.__new__(WeChatGUI)
    g.grabs = 0
    g.render_w, g.render_h = 1200, 1800
    g.right_pane_left = 300
    g.origin_x = g.origin_y = 0
    g._pending_text = None
    g._get_uia = lambda refresh=False: uia

    def _grab(box):
        g.grabs += 1
        return BoomImage()        # 一旦被真 OCR 用到就炸，证明闸在截图之后没漏

    g._grab_screen = _grab
    g._ocr_rows = list(ocr_rows)
    return g


TXT = "文件传输助手"

print("--- A. 关掉 OCR：一律空结果，且不截图不调引擎 ---")
WxParam.ENABLE_OCR = False
check("ScreenOCR.available()", ScreenOCR.available(), False)
check("recognize 返回空", ScreenOCR.recognize(BoomImage()), [])
g = make_gui()
check("ocr() 返回空", g.ocr((0, 0, 100, 100)), [])
check("ocr() 没截图", g.grabs, 0)
check("ocr_zoomed() 返回空", g.ocr_zoomed((0, 0, 100, 100), scale=3), [])
check("ocr_zoomed() 没截图", g.grabs, 0)
check("recognize 对 None 也不炸", ScreenOCR.recognize(None), [])

print("\n--- B. _chat_is_open 在 OCR 关闭时改走 UIA ---")
check("UIA 命中 → True", make_gui(FakeUIA(TXT))._chat_is_open(TXT), True)
check("UIA 读到带装饰的名字 → 包含也算命中",
      make_gui(FakeUIA(TXT + " (12)"))._chat_is_open(TXT), True)
check("UIA 是别的会话 → False", make_gui(FakeUIA("同学群"))._chat_is_open(TXT), False)
check("UIA 没有值 → False（没 OCR 可用）", make_gui(FakeUIA(None))._chat_is_open(TXT), False)
check("UIA 抛异常 → 不炸，退回 False", make_gui(FakeUIA(raises=True))._chat_is_open(TXT), False)
check("UIA 不可用 → False", make_gui(None)._chat_is_open(TXT), False)

print("\n--- C. 打开 OCR：旧行为仍在 ---")
WxParam.ENABLE_OCR = True
check("available()", ScreenOCR.available(), True)

real_recognize = ScreenOCR.recognize
ScreenOCR.recognize = staticmethod(lambda img: [("件传输助", 40, 60, 90, 20)])
g2 = make_gui(uia=None)


def _counting_grab(box):
    g2.grabs += 1
    return FakeImg()              # 生产代码会 resize，BoomImage 会炸


g2._grab_screen = _counting_grab
r = g2.ocr((0, 0, 1200, 185))
check("OCR 开时真去截图", g2.grabs, 1)
check("OCR 结果原样返回", r, [("件传输助", 40, 60, 90, 20)])
check("没有 UIA 时用 OCR 片段匹配（首字符被截掉也能命中）",
      g2._chat_is_open(TXT), True)
n_before = g2.grabs
check("放大档也截图", (g2.ocr_zoomed((0, 0, 1200, 185), scale=3), g2.grabs - n_before)[1], 1)
ScreenOCR.recognize = staticmethod(real_recognize)

# UIA 有值时不该再花 OCR
calls = []
g3 = make_gui(FakeUIA(TXT))
g3.ocr = lambda rel: calls.append(rel) or []
g3.ocr_zoomed = lambda rel, scale=3: calls.append(rel) or []
check("UIA 命中时不调 OCR", g3._chat_is_open(TXT) and calls, [])

print("\n--- D. 闸是不是唯一入口（防以后又长出第二个 OCR 调用点）---")
src_dir = os.path.join(SRC, "wechatauto")
hits = []
for fn in os.listdir(src_dir):
    if not fn.endswith(".py") or fn.startswith("demo_"):
        continue
    t = open(os.path.join(src_dir, fn), encoding="utf-8", errors="replace").read()
    if "OcrEngine" in t:
        hits.append(fn)
check("直接引用 OcrEngine 的模块只有 guia.py", hits, ["guia.py"])
body = inspect.getsource(ScreenOCR.recognize)
check("闸门在引擎调用之前", body.index("available()") < body.index("OcrEngine"), True)

print("\n结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
for n in FAILED:
    print("  失败:", n)
sys.exit(1 if FAILED else 0)
