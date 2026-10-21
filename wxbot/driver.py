# -*- coding: utf-8 -*-
"""驱动层：封装 wechatauto-replica（DB 读 + UIA 写）。"""
import logging
import re
import time

from wechatauto.db import WeChatDB
from wechatauto.uia_driver import WeChatUIA

from . import group_logic

log = logging.getLogger("wxbot.driver")

_SENSITIVE = re.compile(r"\d{15,}|卡号|开户|身份证|密码")
_SEND_ATTEMPTS = 3


class WxDriver:
    def __init__(self):
        self.db = WeChatDB()
        self.uia = WeChatUIA()
        if not self.uia.ensure_window():
            raise RuntimeError("找不到微信主窗口（需已登录且窗口存在）")
        info = self.db.get_self_info() or {}
        self.self_id = info.get("username") or info.get("wxid") or ""
        self.self_name = info.get("nick_name") or info.get("nickname") or self.self_id
        log.info("驱动就绪 self=%s(%s)", self.self_id, self.self_name)

    # ---------- 联系人解析 ----------
    def resolve_id(self, display):
        return self.db.username_by_nickname(display)

    def display_of(self, user_id):
        try:
            return self.db.get_nickname(user_id) or user_id
        except Exception:
            return user_id

    def list_recent_chats(self, limit=60):
        """最近的会话（含群），供 --list 填配置用。"""
        out = []
        for s in self.db.get_sessions(limit=limit):
            u = s.get("username") or ""
            if not u:
                continue
            out.append({"id": u, "display": self.display_of(u),
                        "is_group": u.endswith("@chatroom")})
        return out

    def resolve_group_id(self, display):
        """按群名在最近会话里找 @chatroom id（先精确、后包含，忽略空格）。
        群名常带 emoji 前缀，所以包含匹配是主路径。返回 (id, 群名) 或 None。"""
        key = (display or "").strip()
        if not key:
            return None
        names = {}
        for s in self.db.get_sessions(limit=500):
            u = s.get("username") or ""
            if u.endswith("@chatroom") and u not in names:
                names[u] = self.display_of(u) or ""
        for u, n in names.items():
            if n == key:
                return u, n
        for u, n in names.items():
            if key in n.replace(" ", ""):
                return u, n
        return None

    # ---------- 轮询 ----------
    def baseline(self, user_id, lookback=50):
        msgs = self.db.get_messages(user_id, limit=lookback)
        return max((m["sort_seq"] for m in msgs), default=0)

    def poll(self, user_id, since):
        """返回 (规范化消息列表[升序], 新 since)。"""
        new = self.db.get_new_messages(user_id, since)
        if not new:
            return [], since
        since = max(m["sort_seq"] for m in new)
        is_group = user_id.endswith("@chatroom")
        out = []
        for m in new:
            item = {
                "sender": m.get("sender_username") or "",
                "type": m.get("type"),
                "content": (m.get("content") or "").strip(),
                "ts": m.get("create_time"),
                "seq": m["sort_seq"],
                "is_self": (m.get("sender_username") == self.self_id),
                "is_group": is_group,
            }
            if is_group:
                sender, body = group_logic.parse_group_content(item["content"])
                if item["sender"]:
                    # 行内有发送者：剥离正文里重复的前缀
                    if sender == item["sender"]:
                        item["content"] = body
                elif sender:
                    # 行内无发送者：从正文前缀取
                    item["sender"], item["content"] = sender, body
            item["sender_display"] = self.display_of(item["sender"]) if item["sender"] else ""
            out.append(item)
        return out, since

    def mention_names(self, group_cfg):
        names = [self.self_name] + list((group_cfg.get("manage") or {}).get("mention_names")
                                        or group_cfg.get("mention_names") or [])
        return [n for n in dict.fromkeys(names) if n]

    # ---------- 上下文 ----------
    def build_context(self, user_id, limit=16, speaker_map=None):
        """最近对话行（"我: x" / "昵称: x"），过滤隐私与超长。"""
        msgs = self.db.get_messages(user_id, limit=limit * 2)
        lines = []
        for m in reversed(msgs):
            c = self._friendly(m)
            if not c:
                continue
            if user_id.endswith("@chatroom"):
                who = "我" if (m.get("sender_username") == self.self_id) else \
                    self.display_of(group_logic.parse_group_content(str(m.get("content") or ""))[0]
                                    or m.get("sender_username") or "群友")
            else:
                who = "我" if m.get("sender_username") == self.self_id else "对方"
            lines.append(f"{who}: {c}")
            if len(lines) >= limit:
                break
        return lines

    def _friendly(self, m):
        c = (m.get("content") or "").strip()
        if c.startswith("<?xml"):
            if "<img" in c:
                return "[图片]"
            t = re.search(r"<title>(.*?)</title>", c, re.S)
            return f"[链接:{t.group(1)[:20]}]" if t else "[卡片消息]"
        c = c.replace("\n", " ")
        if _SENSITIVE.search(c):
            return "[略：涉及隐私信息]"
        return c[:100]

    # ---------- 发送 ----------
    def send_text(self, display, text):
        """安全发送：open_chat → 硬校验会话 → 粘贴比对回车，重试 _SEND_ATTEMPTS 次。"""
        why = "未尝试"
        for _ in range(_SEND_ATTEMPTS):
            try:
                if self.uia.open_chat(display):
                    time.sleep(0.8)
                    cur = self.uia.current_chat() or ""
                    if display not in cur:
                        why = f"会话校验失败: {cur!r}"
                    elif self.uia.send_text(text):
                        return True, "ok"
                    else:
                        why = "send_text 返回 False"
                else:
                    why = "open_chat 返回 False"
            except Exception as e:
                why = f"异常: {type(e).__name__}: {e}"
            time.sleep(3)
        return False, f"{why}（重试{_SEND_ATTEMPTS}次后放弃）"
