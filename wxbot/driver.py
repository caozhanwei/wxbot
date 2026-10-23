# -*- coding: utf-8 -*-
"""驱动层：封装 wechatauto-replica（DB 读 + UIA 写）。"""
import logging
import os
import re
import tempfile
import time

from wechatauto.db import WeChatDB
from wechatauto.media import MediaDownloader
from wechatauto.uia_driver import WeChatUIA

from . import group_logic
from .ocr import OcrClient
from .asr import AsrClient

log = logging.getLogger("wxbot.driver")

_SENSITIVE = re.compile(r"\d{15,}|卡号|开户|身份证|密码")
_SEND_ATTEMPTS = 3


def parse_refer_msg(raw_content: str):
    """解析微信 57 类型的引用消息 XML，提取被引用人、被引用内容与本次回复。"""
    if not raw_content or "refermsg" not in raw_content:
        return None
    try:
        title_m = re.search(r"<title>(.*?)</title>", raw_content, re.S)
        reply = title_m.group(1).strip() if title_m else ""
        name_m = re.search(r"<displayname>(.*?)</displayname>", raw_content, re.S)
        quoted_user = name_m.group(1).strip() if name_m else "群友"
        refer_block = re.search(r"<refermsg>(.*?)</refermsg>", raw_content, re.S)
        quoted_content = ""
        if refer_block:
            c_m = re.search(r"<content>(.*?)</content>", refer_block.group(1), re.S)
            if c_m:
                quoted_content = c_m.group(1).strip()
        return {
            "is_quote": True,
            "reply": reply,
            "quoted_user": quoted_user,
            "quoted_content": quoted_content,
        }
    except Exception:
        return None


class WxDriver:
    def __init__(self):
        self.db = WeChatDB()
        self.uia = WeChatUIA()
        if not self.uia.ensure_window():
            raise RuntimeError("找不到微信主窗口（需已登录且窗口存在）")
        info = self.db.get_self_info() or {}
        self.self_id = info.get("username") or info.get("wxid") or ""
        self.self_name = info.get("nick_name") or info.get("nickname") or self.self_id
        self.ocr = OcrClient(None)   # 未配置；Bot 启动时 setup_ocr() 注入
        self.asr = AsrClient(None)   # 未配置；Bot 启动时 setup_asr() 注入
        self._media = None
        self._ocr_cache = {}         # (user, local_id) -> 识别文字（''=已试过没有/失败）
        self._voice_cache = {}       # (user, local_id) -> 转录文字（''=已试过没有/失败）
        log.info("驱动就绪 self=%s(%s) ocr=%s asr=%s", self.self_id, self.self_name,
                 "on" if self.ocr.ready else "off",
                 "on" if self.asr.ready else "off")

    def setup_ocr(self, ocr_cfg):
        """按配置启用/重建 OCR 客户端（热重载后可再调）；清空识别缓存。"""
        client = OcrClient(ocr_cfg)
        if client.ready != self.ocr.ready or getattr(client, "url", "") != self.ocr.url:
            self._ocr_cache.clear()
        self.ocr = client
        log.info("OCR %s（url=%s）", "已启用" if client.ready else "未配置，图片降级为占位",
                 client.url or "-")

    def setup_asr(self, asr_cfg):
        """按配置启用/重建 ASR 客户端；清空转写缓存。"""
        client = AsrClient(asr_cfg)
        if client.ready != self.asr.ready or getattr(client, "url", "") != self.asr.url:
            self._voice_cache.clear()
        self.asr = client
        log.info("ASR %s（url=%s）", "已启用" if client.ready else "未配置，语音降级为占位",
                 client.url or "-")

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

    def search_chats(self, q, limit=12):
        """按昵称/群名/ID 模糊搜最近会话（忽略大小写与空格，子串匹配）。

        供 Web 页"最近会话选择器"与"添加时给候选"使用；返回同 list_recent_chats。
        """
        key = re.sub(r"\s+", "", q or "").casefold()
        if not key:
            return []
        hits = []
        for s in self.db.get_sessions(limit=500):
            u = s.get("username") or ""
            if not u:
                continue
            disp = self.display_of(u) or ""
            if key in re.sub(r"\s+", "", disp + " " + u).casefold():
                hits.append({"id": u, "display": disp, "is_group": u.endswith("@chatroom")})
                if len(hits) >= limit:
                    break
        return hits

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
                "local_id": m.get("local_id"),
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
            ref = parse_refer_msg(item["content"])
            if ref:
                item["is_quote"] = True
                item["quoted_user"] = ref["quoted_user"]
                item["quoted_content"] = ref["quoted_content"]
                if ref["reply"]:
                    item["content"] = ref["reply"]
            if is_group:
                # msgsource 里的 atuserlist 是微信写入的被 @ 精确名单，
                # 比正文文本匹配可靠（群名片≠微信昵称时文本匹配必漏）
                item["at_users"] = group_logic.parse_atuserlist(m.get("source"))
            item["sender_display"] = self.display_of(item["sender"]) if item["sender"] else ""
            out.append(item)
        return out, since

    def mention_names(self, group_cfg):
        names = [self.self_name] + list((group_cfg.get("manage") or {}).get("mention_names")
                                        or group_cfg.get("mention_names") or [])
        return [n for n in dict.fromkeys(names) if n]

    def chat_owner(self, gid):
        """群主 wxid（定时任务等权限判断用）；查不到返回空串。"""
        try:
            return self.db.get_room_owner(gid) or ""
        except Exception:
            return ""

    # ---------- 上下文 ----------
    def build_context(self, user_id, limit=16, speaker_map=None):
        """最近对话行（"我: x" / "昵称: x"），过滤隐私与超长；图片尽量 OCR 出文字。"""
        msgs = self.db.get_messages(user_id, limit=limit * 2)
        lines = []
        for m in reversed(msgs):
            c = self._friendly(m, user_id)
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

    def image_text(self, user_id, local_id):
        """解密群图片并 OCR；结果缓存（含失败），失败返回空串。"""
        if not local_id:
            return ""
        key = (user_id, local_id)
        if key in self._ocr_cache:
            return self._ocr_cache[key]
        text = ""
        try:
            save_dir = os.path.join(tempfile.gettempdir(), "wxbot_ocr")
            if not self._media:
                self._media = MediaDownloader(self.db, save_dir=save_dir)
            path = self._media.download_media(user_id, local_id, save_dir)
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    text = self.ocr.ocr(f.read())[:120]
        except Exception as e:
            log.info("图片OCR失败 %s#%s: %s", user_id, local_id, e)
        if text:
            log.info("图片OCR %s#%s: %s", user_id, local_id, text[:60])
        self._ocr_cache[key] = text
        return text

    def voice_text(self, user_id, local_id):
        """解密群/私聊语音并送 ASR 转写；结果缓存，未配置/失败返回空串。"""
        if not local_id:
            return ""
        key = (user_id, local_id)
        if key in self._voice_cache:
            return self._voice_cache[key]
        text = ""
        try:
            save_dir = os.path.join(tempfile.gettempdir(), "wxbot_voice")
            if not self._media:
                self._media = MediaDownloader(self.db, save_dir=save_dir)
            path = self._media.download_voice(user_id, local_id, save_dir)
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
                text = self.asr.transcribe(data, filename=os.path.basename(path)) or ""
        except Exception as e:
            log.info("语音转写失败 %s#%s: %s", user_id, local_id, e)
        if text:
            log.info("语音转写成功 %s#%s: %s", user_id, local_id, text[:60])
        self._voice_cache[key] = text
        return text

    def member_lines(self, user_id, wxid, limit=20):
        """某成员在会话里的最近发言（_friendly 规整、脱敏、图片 OCR）。"""
        msgs = self.db.get_messages(user_id, limit=200)
        lines = []
        for m in reversed(msgs):
            who = m.get("sender_username") or ""
            body = str(m.get("content") or "")
            if user_id.endswith("@chatroom"):
                sender, parsed = group_logic.parse_group_content(body)
                who = who or sender
                body = parsed
            if who != wxid:
                continue
            f = self._friendly(dict(m, content=body), user_id)
            if f and not f.startswith("[略"):
                lines.append(f)
            if len(lines) >= limit:
                break
        return lines

    def _friendly(self, m, user_id=None):
        c = (m.get("content") or "").strip()
        mtype = str(m.get("type") or "")
        if mtype == "voice" or m.get("local_type") == 34:
            txt = self.voice_text(user_id, m.get("local_id")) if user_id and hasattr(self, "voice_text") else ""
            return f"[语音:{txt[:60]}]" if txt else "[语音]"
        ref = parse_refer_msg(c)
        if ref:
            q_user = ref["quoted_user"]
            q_txt = ref["quoted_content"].replace("\n", " ")[:50]
            rep = ref["reply"].replace("\n", " ")[:80]
            return f'[引用 {q_user}: "{q_txt}"] {rep}'
        if c.startswith("<?xml") or "<msg>" in c:
            if "<img" in c:
                text = self.image_text(user_id, m.get("local_id")) if user_id else ""
                return f"[图片:{text[:60]}]" if text else "[图片]"
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

    def send_quote(self, display, text, target_name="", target_content=""):
        """带气泡引用发送：按提问正文定位那条消息（答题慢、期间别人插话
        也不引错人），定位不到降级为带 @提问人 前缀的普通发送。"""
        try:
            if hasattr(self.uia, "quote_last_message"):
                if self.uia.quote_last_message(display, text, target_text=target_content):
                    return True, "ok"
        except Exception as e:
            log.warning("原生引用发送异常，降级发送: %s", e)
        prefix = f"「回复 @{target_name}」" if target_name else "「引用回复」"
        return self.send_text(display, f"{prefix} {text}")

    def kick_member(self, display, member, probe=""):
        """移出群成员（登录号需为群主/管理员）。任何一步失败返回 False。

        不做任何降级重试——踢人是破坏性动作，宁可失败交人工，绝不误伤。
        """
        try:
            return bool(self.uia.kick_member(display, member, probe=probe))
        except Exception as e:
            log.warning("移出群聊异常(%s/%s): %s", display, member, e)
            return False


WeChatDriver = WxDriver
