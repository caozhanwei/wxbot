# -*- coding: utf-8 -*-
"""消息路由：@机器人答疑 / 管理员命令 / 进群欢迎 / 违禁词 / 问答挖掘。"""
import logging
import re
import threading

from adapter import MSG_SYSTEM
from mining.detector import is_question

log = logging.getLogger("router")

_JOIN_PATTERNS = [
    re.compile(r"邀请\"(.+?)\"加入了群聊"),
    re.compile(r"\"(.+?)\"通过扫描.*?二维码.*?加入群聊"),
    re.compile(r"\"(.+?)\"加入了群聊"),
]


class Router:
    def __init__(self, config, db, wechat, kb, tracker, admin, gmgr):
        self.config = config
        self.db = db
        self.wechat = wechat
        self.kb = kb
        self.tracker = tracker
        self.admin = admin            # admin/commands.py AdminCommands
        self.gmgr = gmgr              # gmgr/__init__.py GroupManager
        self._pool = threading.Semaphore(4)   # 限制并行 LLM/KB 调用
        self.paused = False

    # ---------- 主入口（adapter 回调，跑在接收线程） ----------
    def dispatch(self, msg):
        if msg.type == MSG_SYSTEM:
            self._on_system(msg)
            return
        if msg.type != 1:
            return
        if msg.is_at_bot:
            self._answer_async(msg)
            return
        if msg.content.startswith("#") and not msg.is_group:
            self.admin.handle(msg)      # 私聊命令
            return
        if msg.is_group and msg.sender in self.config["admins"] and msg.content.startswith("#"):
            self.admin.handle(msg)
            return
        if self.paused:
            return
        # 普通消息：违禁词 + 问答挖掘
        if msg.is_group:
            self.gmgr.check_forbidden(msg)
            self.tracker.feed(msg)

    # ---------- @机器人答疑 ----------
    def _answer_async(self, msg):
        def worker():
            with self._pool:
                self._answer(msg)
        threading.Thread(target=worker, daemon=True).start()

    def _answer(self, msg):
        question = msg.content.strip()
        if not question:
            self.wechat.send_text("我在的，@我 并直接说问题就行～", msg.chat_id, aters=msg.sender)
            return
        if not self.kb.ready:
            log.warning("知识库未配置，问题仅落库: %s", question[:60])
            self.db.create_question(msg.id, msg.room or "direct", msg.sender, question)
            self.wechat.send_text("知识库还没配置好，我先记下问题了，稍后人工回复你～",
                                  msg.chat_id, aters=msg.sender)
            return
        try:
            answer, hit, grounded = self.kb.ask(question, msg.room or f"direct_{msg.sender}")
        except Exception as e:
            log.error("知识库调用失败: %s", e)
            self.db.create_question(msg.id, msg.room or "direct", msg.sender, question)
            self.wechat.send_text("我暂时连不上知识库，问题已记录，稍后会有人回复～",
                                  msg.chat_id, aters=msg.sender)
            self._notify_admins(f"[答疑失败] {question[:80]}: {e}")
            return
        if hit:
            text = f"@{self._name_of(msg)} \n{answer}"
            self.wechat.send_text(text, msg.chat_id, aters=msg.sender)
            self.db.audit("answer", "hit", question[:80])
        elif grounded:
            # 未正中但答案带引用来源（如"未提及 X，相近的是 Y"），仍有参考价值：发送并记录待沉淀
            self.db.create_question(msg.id, msg.room or "direct", msg.sender, question)
            text = f"@{self._name_of(msg)} 知识库没有完全正中的答案，以下是相关内容供参考：\n{answer}"
            self.wechat.send_text(text, msg.chat_id, aters=msg.sender)
            self._notify_admins(f"[待沉淀问题] {msg.sender}: {question[:120]}（已有相近答案，可整理入库）")
            self.db.audit("answer", "grounded", question[:80])
        else:
            self.db.create_question(msg.id, msg.room or "direct", msg.sender, question)
            self.wechat.send_text(
                f"@{self._name_of(msg)} 知识库里暂时没有这个问题的答案，已记录，会有同学跟进～",
                msg.chat_id, aters=msg.sender)
            self._notify_admins(f"[未命中问题] {msg.sender}: {question[:120]}")
            self.db.audit("answer", "miss", question[:80])

    def _name_of(self, msg):
        return msg.sender_name or msg.sender

    def _notify_admins(self, text):
        for admin in self.config["admins"]:
            self.wechat.send_text(text, admin)

    # ---------- 系统消息（10000）：进群欢迎 ----------
    def _on_system(self, msg):
        if not msg.is_group:
            return
        joined = None
        for pat in _JOIN_PATTERNS:
            m = pat.search(msg.content)
            if m:
                joined = m.group(1)
                break
        if joined:
            self.gmgr.welcome(msg.room, joined)
