# -*- coding: utf-8 -*-
"""配置加载与热重载。"""
import logging
import os
import threading

import yaml

log = logging.getLogger("wxbot.config")

_DEFAULT_PERSONA = {
    "style": "口语化、简短、自然",
    "background": "",
}


class BotConfig:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = 0
        self.data = {}
        self.reload()

    def reload(self):
        with open(self.path, encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}
        self._mtime = os.path.getmtime(self.path)
        log.info("配置已加载: %s", self.path)

    def maybe_reload(self):
        """文件变化时热重载，返回是否重载了。"""
        try:
            if os.path.getmtime(self.path) != self._mtime:
                self.reload()
                return True
        except OSError:
            pass
        return False

    # ---------- app ----------
    @property
    def app(self):
        return self.data.get("app") or {}

    @property
    def dry_run(self):
        return bool(self.app.get("dry_run"))

    @property
    def web_port(self):
        return int(self.app.get("web_port", 8778))

    @property
    def db_file(self):
        return self.app.get("db_file", "wxbot.db")

    # ---------- llm / kb ----------
    @property
    def llm_cfg(self):
        return self.data.get("llm") or {}

    @property
    def kb_cfg(self):
        return self.data.get("kb") or {}

    @property
    def ocr_cfg(self):
        return self.data.get("ocr") or {}

    @property
    def reply_cfg(self):
        return self.data.get("reply") or {}

    @property
    def admins(self):
        return list(self.data.get("admins") or [])

    # ---------- personas ----------
    def persona(self, name):
        p = (self.data.get("personas") or {}).get(name)
        if not p:
            return dict(_DEFAULT_PERSONA)
        merged = dict(_DEFAULT_PERSONA)
        merged.update(p)
        return merged

    @property
    def persona_names(self):
        return list((self.data.get("personas") or {}).keys())

    # ---------- contacts / groups ----------
    @property
    def contacts(self):
        return list(self.data.get("contacts") or [])

    @property
    def groups(self):
        return list(self.data.get("groups") or [])

    def contact_by_user(self, user_id):
        for c in self.contacts:
            if c.get("id") == user_id:
                return c
        return None

    def group_by_user(self, user_id):
        for g in self.groups:
            if g.get("id") == user_id:
                return g
        return None

    def set_enabled(self, kind, idx, on):
        """Web 页开关（仅内存，重载/重启后以 yaml 为准）。kind: contacts|groups"""
        with self._lock:
            items = self.data.setdefault(kind, [])
            if isinstance(items, list) and 0 <= idx < len(items):
                items[idx]["enabled"] = bool(on)

    def snapshot(self):
        """供 Web 页展示的浅拷贝。"""
        with self._lock:
            import copy
            return copy.deepcopy(self.data)
