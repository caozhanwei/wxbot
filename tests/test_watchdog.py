# -*- coding: utf-8 -*-
"""R4 自愈守护：进程检测/拉起限频/掉线告警/开机自启/崩溃自动重启。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import watchdog as wd


def _mk_bot(wd_cfg=None, poll_fails=0):
    notified, launched = [], []
    bot = type("B", (), {
        "cfg": type("C", (), {"data": {"app": {"watchdog": dict(wd_cfg or {})}}})(),
        "state": {"poll_fails": poll_fails},
    })()
    bot.notify_admins = staticmethod(lambda t: notified.append(t))
    bot._launched = launched
    return bot, notified, launched


def _force_tick(bot):
    bot.state["wd_ts"] = 0                # 越过节流再查一次


# ---------- 纯函数 ----------

def test_wechat_alive_parse():
    assert wd.wechat_alive('"Weixin.exe","123","Console","1","10 K"') is True
    assert wd.wechat_alive('"WeChat.exe","123"') is True
    assert wd.wechat_alive('"chrome.exe","5"') is False
    assert wd.wechat_alive("信息: 没有运行的任务匹配指定标准。") is False


def test_find_wechat_path_prefers_registry(tmp_path):
    a = tmp_path / "Weixin.exe"
    a.write_bytes(b"x")
    old = tmp_path / "WeChatOld.exe"
    old.write_bytes(b"x")
    assert wd.find_wechat_path({"Weixin.exe": str(a)}, [str(old)]) == str(a)
    assert wd.find_wechat_path({}, [str(old)]) == str(old)
    assert wd.find_wechat_path({}, []) is None


# ---------- 巡检：拉起与告警 ----------

def test_dead_wechat_alerts_once_per_hour(monkeypatch):
    monkeypatch.setattr(wd, "_tasklist", lambda name: "没有任务")
    bot, notified, _ = _mk_bot()
    wd.maybe_check(bot)
    _force_tick(bot)
    wd.maybe_check(bot)
    assert len(notified) == 1 and "微信" in notified[0]


def test_dead_wechat_restarts_when_enabled(monkeypatch):
    monkeypatch.setattr(wd, "_tasklist", lambda name: "")
    monkeypatch.setattr(wd, "find_wechat_path",
                        lambda app_paths=None, typical=None: r"C:\T\Weixin.exe")
    bot, notified, launched = _mk_bot({"restart_wechat": True})
    monkeypatch.setattr(wd, "_launch", launched.append)
    wd.maybe_check(bot)
    assert launched == [r"C:\T\Weixin.exe"]
    _force_tick(bot)
    wd.maybe_check(bot)                   # 10 分钟内不重复拉起
    assert len(launched) == 1
    assert len(notified) == 1


def test_dead_wechat_no_restart_by_default(monkeypatch):
    monkeypatch.setattr(wd, "_tasklist", lambda name: "")
    monkeypatch.setattr(wd, "find_wechat_path",
                        lambda app_paths=None, typical=None: r"C:\T\Weixin.exe")
    bot, notified, launched = _mk_bot()
    wd.maybe_check(bot)
    assert launched == [] and len(notified) == 1


def test_alive_wechat_no_alert(monkeypatch):
    monkeypatch.setattr(wd, "_tasklist", lambda name: '"Weixin.exe","9"')
    bot, notified, _ = _mk_bot()
    wd.maybe_check(bot)
    assert notified == []


def test_poll_fails_alert_once(monkeypatch):
    monkeypatch.setattr(wd, "_tasklist", lambda name: '"Weixin.exe","9"')
    bot, notified, _ = _mk_bot(poll_fails=5)
    wd.maybe_check(bot)
    assert len(notified) == 1 and "读消息" in notified[0]
    _force_tick(bot)
    wd.maybe_check(bot)
    assert len(notified) == 1


def test_poll_fails_below_threshold_silent(monkeypatch):
    monkeypatch.setattr(wd, "_tasklist", lambda name: '"Weixin.exe","9"')
    bot, notified, _ = _mk_bot(poll_fails=2)
    wd.maybe_check(bot)
    assert notified == []


# ---------- 开机自启 ----------

def test_set_startup_write_and_delete():
    wrote, deleted = [], []
    wd.set_startup(True, exe=r"C:\x\wxbot.exe",
                   set_fn=lambda n, v: wrote.append((n, v)),
                   del_fn=lambda n: deleted.append(n))
    assert wrote == [("wxbot", r"C:\x\wxbot.exe")] and not deleted
    wd.set_startup(False, set_fn=lambda n, v: wrote.append((n, v)),
                   del_fn=lambda n: deleted.append(n))
    assert deleted == ["wxbot"]


def test_autostart_enabled_flag():
    assert wd.autostart_enabled(get_fn=lambda n: r"C:\x\wxbot.exe") is True
    assert wd.autostart_enabled(get_fn=lambda n: None) is False


# ---------- BotShell 崩溃自动重启 ----------

def test_schedule_restart_respects_config(tmp_path):
    from wxbot import app as app_mod
    timers = []

    def _factory(delay, fn):
        timers.append((delay, fn))
        return None
    shell = type("S", (), {"cfg": type("C", (), {
        "data": {"app": {"watchdog": {"bot_restart": False}}}})()})()
    app_mod._schedule_restart(shell, timer_factory=_factory)
    assert timers == []                   # 配置关掉就不排程

    shell2 = type("S", (), {"cfg": type("C", (), {
        "data": {"app": {"watchdog": {}}}})()})()
    shell2.restarted = False
    shell2.restart = lambda: setattr(shell2, "restarted", True)
    app_mod._schedule_restart(shell2, timer_factory=_factory)
    assert len(timers) == 1 and timers[0][0] == 30
    timers[0][1]()                        # 到点执行
    assert shell2.restarted is True
