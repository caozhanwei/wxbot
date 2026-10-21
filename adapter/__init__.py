# -*- coding: utf-8 -*-
"""微信接入层：真实 wcferry 适配器 + 测试用 Mock，统一 Message 模型与限速发送队列。"""
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Queue

log = logging.getLogger("adapter")

# wcf 消息类型（用到的）
MSG_TEXT = 1
MSG_SYSTEM = 10000


@dataclass
class Message:
    id: int = 0                 # 数据库 id（落库后回填）
    ts: int = 0
    room: str = ""              # 群 id，私聊为空
    sender: str = ""            # 发送者 wxid
    sender_name: str = ""       # 群内显示昵称
    type: int = MSG_TEXT
    content: str = ""
    is_at_bot: bool = False
    is_group: bool = True
    raw_xml: str = ""

    @property
    def chat_id(self) -> str:
        """回复目标：群消息回群，私聊回对方。"""
        return self.room if self.is_group else self.sender


class SendQueue:
    """串行发送 + 最小间隔 + 随机抖动，防风控。dry_run 时只记日志不发送。"""

    def __init__(self, sender, rate_limit_seconds=5, jitter=(3, 8), dry_run=True):
        self.sender = sender            # callable(text, receiver, aters) -> None
        self.rate = float(rate_limit_seconds)
        self.jitter = jitter
        self.dry_run = dry_run
        self._q: Queue = Queue()
        self._last_sent = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="send-queue")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def submit(self, text, receiver, aters=None):
        self._q.put((text, receiver, aters))

    def _run(self):
        while not self._stop.is_set():
            try:
                text, receiver, aters = self._q.get(timeout=1)
            except Empty:
                continue
            with self._lock:
                wait = self._last_sent + self.rate - time.time()
                if wait > 0:
                    time.sleep(wait)
                jitter = random.uniform(*self.jitter) if self.jitter else 0
                if jitter:
                    time.sleep(jitter)
                if self.dry_run:
                    log.info("[dry-run] 发送 -> %s aters=%s: %s", receiver, aters, text.replace("\n", " ")[:120])
                else:
                    try:
                        self.sender(text, receiver, aters)
                    except Exception as e:
                        log.error("发送失败 receiver=%s: %s", receiver, e)
                self._last_sent = time.time()


class WeChatAdapter:
    """真实接入：wcferry。需要本机微信 PC 版（版本需与 wcferry 匹配）已登录。"""

    def __init__(self, config, db):
        from wcferry import Wcf  # 延迟导入，无微信环境跑测试不装也能 mock
        self.config = config
        self.db = db
        self.wcf = Wcf(block=True)          # 本地模式：自动起 wcf.exe 注入并等待登录
        self.self_wxid = self.wcf.get_self_wxid()
        log.info("微信登录成功 self_wxid=%s", self.self_wxid)
        self.sendq = SendQueue(
            sender=self._raw_send,
            rate_limit_seconds=config["wcf"]["rate_limit_seconds"],
            jitter=tuple(config["wcf"]["jitter_seconds"]),
            dry_run=config["dry_run"])
        self._name_cache = {}

    def _raw_send(self, text, receiver, aters=None):
        # wcferry 约定：aters 为逗号分隔 wxid，且 text 中需含相同数量的 @
        self.wcf.send_text(text, receiver, aters or "")

    def start(self, on_message):
        self.sendq.start()
        self.wcf.enable_receiving_msg()
        threading.Thread(target=self._recv_loop, args=(on_message,), daemon=True, name="recv").start()

    def _recv_loop(self, on_message):
        from queue import Empty as QEmpty
        while True:
            try:
                m = self.wcf.get_msg(block=True)
            except QEmpty:
                continue
            except Exception as e:
                log.error("收消息异常: %s", e)
                time.sleep(1)
                continue
            try:
                msg = self._convert(m)
                if msg is not None:
                    on_message(msg)
            except Exception as e:
                log.exception("处理消息异常: %s", e)

    def _convert(self, m) -> Message:
        is_group = m.from_group()
        if is_group and self.config["rooms"] and m.room not in self.config["rooms"]:
            return None
        is_at_bot = is_group and f"@{self.self_wxid}" in (m.xml or "")
        # 群文本消息 content 形如 "@botid:\s*正文"，把前缀去掉
        content = m.content or ""
        if is_group and m.type == MSG_TEXT and m.sender != "system":
            for prefix in (f"@{self.self_wxid}:", f"@{self.self_wxid} "):
                if content.startswith(prefix):
                    content = content[len(prefix):].lstrip()
                    break
        sender_name = self._display_name(m.sender, m.room) if is_group else ""
        msg = Message(
            ts=m.ts, room=m.room, sender=m.sender, sender_name=sender_name,
            type=m.type, content=content.strip(), is_at_bot=is_at_bot,
            is_group=is_group, raw_xml=m.xml or "")
        msg.id = self.db.insert_message(msg.ts, msg.room, msg.sender, msg.sender_name,
                                        msg.type, msg.content, msg.is_at_bot, msg.raw_xml)
        return msg

    def _display_name(self, wxid, room):
        key = (room, wxid)
        if key not in self._name_cache:
            try:
                alias = self.wcf.get_alias_in_chatroom(wxid, room) if room else ""
            except Exception:
                alias = ""
            self._name_cache[key] = alias or wxid
        return self._name_cache[key]

    def send_text(self, text, receiver, aters=None):
        self.sendq.submit(text, receiver, aters)

    def room_member_names(self, room):
        try:
            members = self.wcf.get_chatroom_members(room) or {}
            return {wxid: (v.get("name") or wxid) if isinstance(v, dict) else wxid for wxid, v in members.items()}
        except Exception as e:
            log.error("取群成员失败: %s", e)
            return {}


class MockWeChat:
    """测试/开发用：无微信环境。可注入消息、断言已发送内容。接口与 WeChatAdapter 对齐。"""

    def __init__(self, config, db, self_wxid="bot_wxid"):
        self.config = config
        self.db = db
        self.self_wxid = self_wxid
        self.sent = []                       # [(text, receiver, aters)]
        self.sent_lock = threading.Lock()
        self.sendq = SendQueue(
            sender=self._record,
            rate_limit_seconds=0, jitter=(0, 0), dry_run=False)  # 测试不限速
        self._handlers = []
        self._seq = [0]

    def _record(self, text, receiver, aters=None):
        with self.sent_lock:
            self.sent.append((text, receiver, aters))

    def start(self, on_message):
        self.sendq.start()
        self._handlers.append(on_message)

    def inject(self, sender="user_a", room="room_1", content="", mtype=MSG_TEXT,
               sender_name="", is_at_bot=False, xml=""):
        """模拟一条来自群/私聊的消息，走完整处理管线。"""
        is_group = bool(room)
        if is_group and self.config["rooms"] and room not in self.config["rooms"]:
            return
        self._seq[0] += 1
        ts = int(time.time())
        msg = Message(ts=ts, room=room or "", sender=sender, sender_name=sender_name,
                      type=mtype, content=content, is_at_bot=is_at_bot,
                      is_group=is_group, raw_xml=xml)
        msg.id = self.db.insert_message(ts, msg.room, sender, sender_name, mtype,
                                        content, is_at_bot, xml)
        for h in self._handlers:
            h(msg)
        return msg

    def send_text(self, text, receiver, aters=None):
        self.sendq.submit(text, receiver, aters)

    def room_member_names(self, room):
        return {}

    def wait_sent(self, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.sent_lock:
                if self.sent:
                    return self.sent
            time.sleep(0.05)
        return []
