# -*- coding: utf-8 -*-
"""群管理：欢迎新人、违禁词监控、每日日报。"""
import logging
import time

log = logging.getLogger("gmgr")


class GroupManager:
    def __init__(self, config, db, wechat):
        self.config = config
        self.db = db
        self.wechat = wechat

    def welcome(self, room, joined_name):
        text = self.config["welcome_text"]
        self.wechat.send_text(f"欢迎 {joined_name}！{text}", room)
        self.db.audit("welcome", "sent", f"{room} <- {joined_name}")

    def check_forbidden(self, msg):
        words = self.config.get("forbidden_words") or []
        hit = next((w for w in words if w and w in msg.content), None)
        if not hit:
            return
        detail = f"群 {msg.room} {msg.sender_name or msg.sender}: {msg.content[:100]}"
        self.db.audit("forbidden", hit, detail)
        for admin in self.config["admins"]:
            self.wechat.send_text(f"[违禁词提醒] 命中“{hit}” {detail}", admin)

    def daily_report(self):
        s = self.db.stats_today()
        lines = [f"📊 群助手日报 {time.strftime('%Y-%m-%d')}", ""]
        lines.append(f"近24h消息: {s['msgs_24h']} 条")
        lines.append(f"新增问题: {s['new_questions_24h']} 个（未解决 {s['open_questions']} 个）")
        lines.append(f"待审核问答对: {s['pending_qa']} 对")
        opens = self.db.questions_by_status("open", limit=10)
        if opens:
            lines.append("")
            lines.append("未解决问题：")
            for q in opens:
                lines.append(f"  #{q['id']} {q['text'][:60]}")
        text = "\n".join(lines)
        sent = False
        for admin in self.config["admins"]:
            self.wechat.send_text(text, admin)
            sent = True
        if not sent and self.config["rooms"]:
            self.wechat.send_text(text, self.config["rooms"][0])
        self.db.audit("report", "daily", f"msgs={s['msgs_24h']} qs={s['new_questions_24h']}")
        return text
