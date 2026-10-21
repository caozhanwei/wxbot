# -*- coding: utf-8 -*-
"""run_gui 回退：无 pywebview 时不能崩，改用默认浏览器打开状态页（CI 即此场景）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import app

CFG = """app: {dry_run: true, web_port: 8791, db_file: t.db}
llm: {base_url: "http://x/v1", api_key: k, model: m}
"""


def test_run_gui_falls_back_to_browser(tmp_path, monkeypatch):
    cfg = tmp_path / "bot_config.yaml"
    cfg.write_text(CFG, encoding="utf-8")

    # 强制 import webview 抛 ImportError（即便开发机装了 pywebview）
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(app.webui, "start_web", lambda shell: None)
    monkeypatch.setattr(app.BotShell, "restart", lambda self: None)
    monkeypatch.setattr(app, "_keepalive", lambda: None)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    app.run_gui(str(cfg))                     # 不应抛异常
    assert opened == ["http://127.0.0.1:8791"]  # 浏览器拿到正确地址
