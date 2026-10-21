"""发送前回读校验的离线自测（不碰微信、不注键盘、不发消息）。

跑法：python tools/test_send_verify.py
覆盖：理想答案必须过 / 每条判据各配一条错答案 / 拦下时确实没有按回车。
"""
import os
import sys
import types

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

from wechatauto import uia_driver                      # noqa: E402
from wechatauto.param import WxParam                   # noqa: E402

DEFAULT_RATIO = WxParam.SEND_CONTENT_RATIO


class _Pattern:
    def __init__(self, value):
        self.Value = value


class StubEdit:
    """假输入框：记下键序，读回值可控（value=None 表示控件不暴露值）。"""

    def __init__(self, value):
        self.value = value
        self.keys = []

    def SendKeys(self, keys, waitTime=0):
        self.keys.append(keys)

    def GetValuePattern(self):
        return None if self.value is None else _Pattern(self.value)

    def GetLegacyIAccessiblePattern(self):
        return None


def make_driver(value):
    """绕开 __init__（它会 CoInitialize、找窗口），只要被测方法。"""
    eng = object.__new__(uia_driver.WeChatUIA)
    box = StubEdit(value)
    eng.ensure_window = lambda: True
    eng._chat_input = lambda win=None: box
    eng._paste_into = lambda ctrl, text, clear=True: None   # 不真去点、不真去粘
    return eng, box


PASSED, FAILED = [], []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-40s got=%-8s want=%s" % ("ok" if ok else "FAIL", name, got, want))


def landed(read_back, text, ratio=None):
    WxParam.SEND_CONTENT_RATIO = DEFAULT_RATIO if ratio is None else ratio
    eng, _ = make_driver(read_back)
    ok, _got, score = eng._paste_landed(StubEdit(read_back), text)
    return round(score, 3), ok


TXT = "今天下午三点开会"

print("--- A. 判据本身（默认阈值 %.2f）---" % DEFAULT_RATIO)
score, ok = landed(TXT, TXT)
check("理想答案：完全一致 → 过", ok, True)
check("理想答案：相似度 1.0", score, 1.0)

score, ok = landed("", TXT)
check("送给空气：读回空 → 拦（ratio=%.3f）" % score, ok, False)

score, ok = landed("昨天那条还没发的旧话", TXT)
check("上一轮残留：读回别的 → 拦（ratio=%.3f）" % score, ok, False)

score, ok = landed("你好 世界", "你好\n世界")
check("归一化：换行/空格差 → 过", ok, True)

# 阈值定 0.9 时这条会误拦，所以默认值是 0.6
score, ok = landed(TXT, TXT[:-1] + "雾")
check("短消息一字之差 → 过（ratio=%.3f）" % score, ok, True)
if DEFAULT_RATIO > score:
    print("     注意：阈值被改成 %s，这条就会变成误拦" % DEFAULT_RATIO)

# 真机口径（2026-10-04 实测 4.1.15.13）：[微笑] 在输入框里是 1 个 U+FFFC，比对前折算
score, ok = landed("好的\ufffc", "好的[微笑]")
check("表情码折算：短消息 → 过（ratio=%.3f）" % score, ok, True)
score, ok = landed("\ufffc", "[微笑]")
check("纯表情码 → 过（ratio=%.3f）" % score, ok, True)
score, ok = landed("好的[微笑]", "好的[微笑]")
check("表情码没转成占位符（别的版本）→ 过（ratio=%.3f）" % score, ok, True)
# 折算不能反过来把真误拦也洗白：正文错了照样拦
score, ok = landed("明天上午八点上课\ufffc", "今天下午三点开会[微笑]")
check("正文错+表情码 → 仍拦（ratio=%.3f）" % score, ok, False)
print("     已知盲区：折算后 [微笑] 与 [发怒] 不可分辨（都变成 1 个 U+FFFC），"
      "表情码打错不会被这道闸拦下。")

score, ok = landed(None, TXT)
check("控件不暴露值 → 放行（不能把探测不到当失败）", ok, True)

score, ok = landed("", TXT, ratio=0)
check("SEND_CONTENT_RATIO<=0 → 校验关闭", ok, True)

print("\n--- B. send_text 拦下时到底按没按回车 ---")
# A 组最后一条把阈值设成了 0（关闭校验），这里必须显式恢复，否则 B 组测的是"关闭"状态
WxParam.SEND_CONTENT_RATIO = DEFAULT_RATIO
uia_driver.rhythm = types.SimpleNamespace(gate=lambda *a: None, nap=lambda *a: None)

eng, box = make_driver(TXT)
check("正常路径：返回 True", eng.send_text(TXT), True)
check("正常路径：打了回车", "{Enter}" in box.keys, True)

eng, box = make_driver("")            # Ctrl+V 落空
check("空气路径：返回 False", eng.send_text(TXT), False)
check("空气路径：没打回车", "{Enter}" in box.keys, False)
check("空气路径：清空了输入框", any("Delete" in k for k in box.keys), True)

eng, box = make_driver("昨天那条还没发的旧话")   # 框里是上一轮残留
check("残留路径：返回 False", eng.send_text(TXT), False)
check("残留路径：没打回车", "{Enter}" in box.keys, False)

eng, box = make_driver(None)          # 读不回值
check("探测不到路径：仍然发送", eng.send_text(TXT), True)
check("探测不到路径：打了回车", "{Enter}" in box.keys, True)

print("\n结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
for n in FAILED:
    print("  失败:", n)
sys.exit(1 if FAILED else 0)
