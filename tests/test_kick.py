# -*- coding: utf-8 -*-
"""#踢 二次确认：UIA 移出群聊编排 + 管理员命令（待确认态/超时/dry_run 演练）。

设计底线：踢人是破坏性动作，每一步找不到入口就放弃并提示人工，
绝不盲点误伤；确认令牌 5 分钟过期；dry_run 只演练不动人。
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_console import FakeDriver, _mk_bot
from wxbot.bot import Bot
from wxbot.config import BotConfig

# ---------- UIA 编排：kick_member ----------

import wechatauto.uia_driver as _ud
from wechatauto.uia_driver import WeChatUIA


class _Lst:
    def __init__(self, left=40):
        self.BoundingRectangle = types.SimpleNamespace(
            left=left, right=left + 600, top=0, bottom=800)


class _Kicker:
    """绕过重量级 __init__ 的 WeChatUIA，按页喂 OCR，桩掉菜单/弹窗点击。"""

    def __init__(self, pages, menu_ok=True, confirm_ok=True, wheel_ok=True):
        self.u = object.__new__(WeChatUIA)
        self.pages, self.menu_ok, self.confirm_ok = list(pages), menu_ok, confirm_ok
        self.wheel_ok = wheel_ok
        self.ocr_calls = 0
        self.scrolled = []
        self.restored = 0
        self.events = []
        self.menu_calls = []
        self.u.ensure_window = lambda: True
        self.u.current_chat = lambda: None
        self.u.open_chat = lambda who: self.events.append(("open", who)) or True
        self.lst = _Lst()
        self.u._message_list = lambda: self.lst
        self.u._ocr_message_area = self._ocr
        self.u._wheel_list = self._wheel
        self.u._restore_chat_bottom = self._restore
        self.u._set_cursor = lambda x, y: self.events.append(("move", x, y))
        self.u._right_click = lambda: self.events.append("rclick")
        self.u._menu_click_tight = self._menu
        self.u._confirm_kick_dialog = self._confirm

    def _ocr(self, lst):
        i = min(self.ocr_calls, len(self.pages) - 1)
        self.ocr_calls += 1
        return self.pages[i]

    def _wheel(self, lst, delta, notches=1, settle=0.2):
        self.scrolled.append((delta, notches))
        return self.wheel_ok

    def _restore(self):
        self.restored += 1

    def _menu(self, label, pos=None):
        self.menu_calls.append((label, pos))
        return self.menu_ok

    def _confirm(self):
        self.events.append("confirm")
        return self.confirm_ok


def _mk_kicker(monkeypatch, pages, **kw):
    monkeypatch.setattr(_ud.rhythm, "nap", lambda s: None)
    monkeypatch.setattr(_ud.rhythm, "gate", lambda kind="write": 0.0)
    return _Kicker(pages, **kw)


def test_kick_happy_visible_first_page(monkeypatch):
    p = _mk_kicker(monkeypatch, [[("加微信领优惠券 私聊我", 60, 300, 200, 20)]])
    ok = p.u.kick_member("群甲", "营销号", probe="加微信领优惠券 私聊我")
    assert ok is True
    assert p.ocr_calls == 1 and p.scrolled == [] and p.restored == 0
    assert ("open", "群甲") in p.events and "rclick" in p.events and "confirm" in p.events
    label, pos = p.menu_calls[0]
    assert label == "移出群聊"
    ax, ay = pos
    assert 40 + 62 <= ax <= 40 + 80          # 头像在消息行左侧
    assert ay == 300 + 20 // 2               # 与正文同一行


def test_kick_empty_probe_never_acts(monkeypatch):
    p = _mk_kicker(monkeypatch, [[("任意", 1, 1, 2, 2)]])
    assert p.u.kick_member("群甲", "营销号", probe="") is False
    assert p.ocr_calls == 0 and p.menu_calls == []


def test_kick_locate_fail_restores_bottom(monkeypatch):
    p = _mk_kicker(monkeypatch, [[("无关内容", 10, 100, 80, 20)]])
    assert p.u.kick_member("群甲", "营销号", probe="加微信领优惠券") is False
    assert p.ocr_calls == 6                       # 6 页封顶
    assert p.scrolled == [(120, 5)] * 5
    assert p.restored == 1 and p.menu_calls == []


def test_kick_scroll_finds_on_second_page(monkeypatch):
    p = _mk_kicker(monkeypatch, [
        [("别人插话刷屏了", 10, 300, 120, 20)],
        [("加微信领优惠券", 10, 300, 120, 20)],
    ])
    assert p.u.kick_member("群甲", "营销号", probe="加微信领优惠券") is True
    assert p.ocr_calls == 2 and p.scrolled == [(120, 5)] and p.restored == 0


def test_kick_menu_missing_fails(monkeypatch):
    p = _mk_kicker(monkeypatch, [[("加微信领优惠券", 10, 300, 120, 20)]],
                   menu_ok=False)
    assert p.u.kick_member("群甲", "营销号", probe="加微信领优惠券") is False
    assert p.menu_calls and "confirm" not in p.events
    assert p.restored == 0                       # 没翻过页就不用恢复


def test_kick_confirm_dialog_missing_fails(monkeypatch):
    p = _mk_kicker(monkeypatch, [[("加微信领优惠券", 10, 300, 120, 20)]],
                   confirm_ok=False)
    assert p.u.kick_member("群甲", "营销号", probe="加微信领优惠券") is False


def test_driver_kick_facade_no_fallback(tmp_path):
    from wxbot import driver as drv

    class _UiaBoom:
        def kick_member(self, who, member, probe=""):
            raise RuntimeError("UIA 挂了")

    class _D(drv.WeChatDriver):
        def __init__(self):
            self.self_id = "me"
            self.self_name = "群助手"
            self.uia = _UiaBoom()

    d = _D()
    assert d.kick_member("群甲", "营销号", probe="x") is False   # 异常也不冒泡


# ---------- 管理员命令：#踢 / #确认踢 / #取消踢 ----------

_AD_MSG = {"sort_seq": 2, "create_time": time.time(), "sender_username": "wxid_ad",
           "sender_nickname": "营销号", "type": "文本",
           "content": "wxid_ad:\n加微信领优惠券 私聊我", "local_id": 2, "source": ""}


def _bot_with_ads(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    b.driver.db.get_messages = lambda gid, limit=500, offset=0: [_AD_MSG]
    return b


def test_kick_cmd_prepares_with_evidence(tmp_path):
    b = _bot_with_ads(tmp_path)
    r = b._do_admin("踢", ["营销号"])
    assert "测试群" in r and "营销号" in r and "#确认踢" in r and "#取消踢" in r
    assert "加微信领优惠券" in r                       # 证据展示
    p = b.state["kick_pending"]
    assert p["member"] == "营销号" and p["gname"] == "测试群"
    assert p["probe"] == "加微信领优惠券 私聊我"


def test_kick_cmd_no_evidence_still_confirmable(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()                                   # FakeDB 空 → 无证据
    r = b._do_admin("踢", ["路人甲"])
    assert "没找到" in r and "#确认踢" in r
    assert b.state["kick_pending"]["probe"] == ""


def test_kick_cmd_refuses_bot_self(tmp_path):
    b = _bot_with_ads(tmp_path)
    assert "自己" in b._do_admin("踢", ["智能机器人"])
    assert not b.state.get("kick_pending")


def test_kick_confirm_dry_run_rehearsal(tmp_path):
    b = _bot_with_ads(tmp_path)
    called = []
    b.driver.kick_member = lambda display, member, probe="": called.append(1) or True
    b._do_admin("踢", ["营销号"])
    r = b._do_admin("确认踢", [])
    assert "演练" in r and not called
    assert not b.state.get("kick_pending")            # 确认即清，防重复执行


def test_kick_confirm_executes_in_background(tmp_path):
    b = _bot_with_ads(tmp_path)
    b.cfg.data["app"]["dry_run"] = False
    calls = []
    b.driver.kick_member = lambda display, member, probe="": \
        calls.append((display, member, probe)) or True
    b._do_admin("踢", ["营销号"])
    assert "正在移出" in b._do_admin("确认踢", [])
    deadline = time.time() + 3
    hit = False
    while time.time() < deadline:
        logs = [r for r in b.store.recent_logs(30)
                if r["kind"] == "notify" and "已移出" in (r["detail"] or "")]
        if calls and logs:
            hit = True
            break
        time.sleep(0.05)
    assert hit and calls == [("测试群", "营销号", "加微信领优惠券 私聊我")]


def test_kick_confirm_failure_prompts_manual(tmp_path):
    b = _bot_with_ads(tmp_path)
    b.cfg.data["app"]["dry_run"] = False
    b.driver.kick_member = lambda display, member, probe="": False
    b._do_admin("踢", ["营销号"])
    b._do_admin("确认踢", [])
    deadline = time.time() + 3
    hit = False
    while time.time() < deadline:
        logs = [r for r in b.store.recent_logs(30)
                if r["kind"] == "notify" and "手动" in (r["detail"] or "")]
        if logs:
            hit = True
            break
        time.sleep(0.05)
    assert hit and "营销号" in logs[0]["detail"]


def test_kick_confirm_expires(tmp_path):
    b = _bot_with_ads(tmp_path)
    b.state["kick_pending"] = {"gid": "111@chatroom", "gname": "测试群",
                               "member": "营销号", "probe": "x",
                               "ts": time.time() - 400}
    r = b._do_admin("确认踢", [])
    assert "超时" in r and "重新" in r and not b.state.get("kick_pending")


def test_kick_cancel(tmp_path):
    b = _bot_with_ads(tmp_path)
    b._do_admin("踢", ["营销号"])
    assert "取消" in b._do_admin("取消踢", [])
    assert not b.state.get("kick_pending")
    assert "没有" in b._do_admin("取消踢", [])


def test_kick_confirm_without_pending(tmp_path):
    b = _bot_with_ads(tmp_path)
    assert "没有" in b._do_admin("确认踢", [])


# ---------- 多群：必须带群名 ----------

def _mk_multi(tmp_path):
    db = str(tmp_path / "m.db").replace("\\", "/")
    text = """
app: {dry_run: true, db_file: "%s"}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m}
admins: ["wxid_admin"]
personas:
  群管家-热心: {style: 热心靠谱, background: 技术群管家}
groups:
  - id: "111@chatroom"
    display: 群甲
    enabled: true
  - id: "222@chatroom"
    display: 群乙
    enabled: true
""" % db
    p = tmp_path / "multi.yaml"
    p.write_text(text, encoding="utf-8")
    return Bot(cfg=BotConfig(str(p)), with_web=False, driver=FakeDriver())


def test_kick_multi_groups_requires_group_name(tmp_path):
    b = _mk_multi(tmp_path)
    b.build_watch()
    r = b._do_admin("踢", ["营销号"])
    assert "群名" in r and "群甲" in r and "群乙" in r
    assert not b.state.get("kick_pending")


def test_kick_multi_groups_with_group_name(tmp_path):
    b = _mk_multi(tmp_path)
    b.build_watch()
    r = b._do_admin("踢", ["营销号", "群乙"])
    assert "群乙" in r and "营销号" in r
    assert b.state["kick_pending"]["gname"] == "群乙"
    assert b.state["kick_pending"]["gid"] == "222@chatroom"
