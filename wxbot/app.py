# -*- coding: utf-8 -*-
"""桌面 GUI 入口：pywebview 窗口承载 Web 状态页，Bot 在后台线程运行。"""
import logging
import threading
import time

from . import paths, webui
from .config import BotConfig
from .store import Store

log = logging.getLogger("wxbot.app")


class BotShell:
    """给 webui 提供稳定外观的对象；真正启动在后台线程，失败可重试。"""

    def __init__(self, cfg_path):
        self.cfg_path = cfg_path
        self.cfg = BotConfig(cfg_path)
        self.store = Store(paths.resolve(self.cfg.db_file))
        self.state = {"paused": False, "sent": 0, "start": time.time()}
        self.startup_error = None
        self._bot = None
        self._thread = None

    # ---------- webui 需要的属性 ----------
    @property
    def driver(self):
        return self._bot.driver if self._bot else None

    @property
    def personas(self):
        return self._bot.personas if self._bot else None

    def build_watch(self):
        if self._bot:
            self._bot.build_watch()

    # ---------- 启停 ----------
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def restart(self):
        if self.running():
            return
        self.startup_error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        from .bot import Bot
        try:
            bot = Bot(cfg_path=self.cfg_path, with_web=False,
                      cfg=self.cfg, state=self.state)
        except Exception as e:
            log.error("Bot 启动失败: %s", e)
            self.startup_error = f"{type(e).__name__}: {e}"
            self._bot = None
            return
        self._bot = bot
        try:
            bot.run()
        except Exception as e:
            log.exception("Bot 运行异常退出")
            self.startup_error = f"运行异常退出: {e}"
            self._bot = None


def run_gui(cfg_path=None):
    cfg_path = cfg_path or paths.default_config()
    shell = BotShell(cfg_path)
    webui.start_web(shell)
    shell.restart()

    import webview
    webview.create_window("微信智能助手", f"http://127.0.0.1:{shell.cfg.web_port}",
                          width=1100, height=800, min_size=(860, 600))
    webview.start()
    log.info("窗口关闭，退出")
