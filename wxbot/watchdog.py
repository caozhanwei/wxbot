# -*- coding: utf-8 -*-
"""R4 自愈守护：微信进程守护拉起 + 掉线告警 + 崩溃自动重启 + 开机自启。

只做恢复不做风控规避：进程不在→告警（可配自动拉起）、连续读消息失败→告警；
bot 异常退出由 app.BotShell 定时拉起重启；开机自启写 HKCU Run 键。
"""
import logging
import os
import subprocess
import sys
import time

log = logging.getLogger("wxbot.watchdog")

CHECK_INTERVAL = 60          # 巡检间隔（秒）
ALERT_EVERY = 3600           # 每类告警每小时最多一次
RELAUNCH_EVERY = 600         # 微信拉起重试间隔
POLL_FAIL_THRESHOLD = 5      # 连续读消息失败几次算掉线
RUN_NAME = "wxbot"

_TYPICAL = [r"C:\Program Files\Tencent\Weixin\Weixin.exe",
            r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
            r"C:\Program Files\Tencent\WeChat\WeChat.exe"]


def wechat_alive(task_out):
    """tasklist 输出里有没有微信进程（4.x=Weixin.exe，3.x=WeChat.exe）。"""
    return ("Weixin.exe" in task_out) or ("WeChat.exe" in task_out)


def find_wechat_path(app_paths=None, typical=None):
    """注册表 App Paths 优先，其次常见安装路径；找不到返回 None。"""
    if app_paths is None:
        app_paths = _app_paths()
    for name in ("Weixin.exe", "WeChat.exe"):
        p = (app_paths or {}).get(name)
        if p and os.path.isfile(p):
            return p
    for p in (typical if typical is not None else _TYPICAL):
        if os.path.isfile(p):
            return p
    return None


def _app_paths():
    import winreg
    out = {}
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for name in ("Weixin.exe", "WeChat.exe"):
            if name in out:
                continue
            try:
                with winreg.OpenKey(
                        root,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
                        + "\\" + name) as k:
                    out[name] = winreg.QueryValueEx(k, "")[0]
            except OSError:
                pass
    return out


def _tasklist(name):
    """tasklist 查指定进程名；失败返回空串（按不存活处理）。"""
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}",
                            "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout or ""
    except Exception as e:
        log.warning("tasklist 失败: %s", e)
        return ""


def _launch(path):
    subprocess.Popen([path], cwd=os.path.dirname(path) or None)
    log.info("已拉起微信: %s", path)


def maybe_check(bot):
    """主循环 60s 节流巡检：微信进程守护 + 掉线告警（各自限频）。"""
    if time.time() - bot.state.get("wd_ts", 0) < CHECK_INTERVAL:
        return
    bot.state["wd_ts"] = time.time()
    cfg = (bot.cfg.data.get("app") or {}).get("watchdog") or {}
    now = time.time()
    alerts = bot.state.setdefault("wd_alert", {})

    if not wechat_alive(_tasklist("Weixin.exe") + _tasklist("WeChat.exe")):
        if now - alerts.get("wechat_gone", 0) > ALERT_EVERY:
            alerts["wechat_gone"] = now
            bot.notify_admins("[守护] 微信进程不在（可能退出或掉线），请检查")
        if cfg.get("restart_wechat") and \
                now - bot.state.get("wd_launch_ts", 0) > RELAUNCH_EVERY:
            path = find_wechat_path()
            if path:
                bot.state["wd_launch_ts"] = now
                try:
                    _launch(path)
                except Exception as e:
                    log.warning("拉起微信失败: %s", e)

    fails = bot.state.get("poll_fails") or 0
    if fails >= POLL_FAIL_THRESHOLD and now - alerts.get("poll", 0) > ALERT_EVERY:
        alerts["poll"] = now
        bot.notify_admins(f"[守护] 连续 {fails} 次读消息失败，微信可能掉线或屏幕已锁定")


# ---------- 开机自启（HKCU Run 键，仅当前用户） ----------

def _run_key():
    import winreg
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", 0,
        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)


def set_startup(enable, exe=None, set_fn=None, del_fn=None):
    """写/删开机自启项；set_fn/del_fn 供测试注入。"""
    if enable:
        exe = exe or (sys.executable if getattr(sys, "frozen", False)
                      else f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"')
        (set_fn or _reg_set)(RUN_NAME, exe)
    else:
        (del_fn or _reg_del)(RUN_NAME)


def autostart_enabled(get_fn=None):
    try:
        return bool((get_fn or _reg_get)(RUN_NAME))
    except OSError:
        return False


def _reg_set(name, value):
    with _run_key() as k:
        import winreg
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _reg_del(name):
    import winreg
    try:
        with _run_key() as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def _reg_get(name):
    import winreg
    with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run") as k:
        return winreg.QueryValueEx(k, name)[0]
