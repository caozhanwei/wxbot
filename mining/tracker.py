# -*- coding: utf-8 -*-
"""问答挖掘追踪器：问题 → 时间窗内他人回复 → 感谢词/管理员确认 → LLM 抽取问答对(pending)。

状态机：
  普通消息 ──is_question──> questions(open)
  open 问题所在群收到他人非提问消息 → 记为候选回复
  提问者发感谢词（且存在候选回复）→ 抽取问答对 → qa_pairs(pending)，question→answered
  超时(窗口×2)未解决 → question→expired（仍可在 web 审核页人工补答案）
"""
import logging
import threading
import time

from .detector import is_question

log = logging.getLogger("tracker")

_EXTRACT_SYSTEM = """你是问答对整理助手。输入是一段群聊中提取的问题和若干候选回复消息。
请提炼出一对干净的"问答"：
- q：把问题整理成一句完整、自包含的问句（保留专业术语、命令名、版本号，不翻译英文术语）
- a：综合候选回复整理出可直接作为答案的回复；若多条回复互补则合并；与问题无关的内容丢弃
若候选回复中没有能回答该问题的内容，输出 {"q": "", "a": ""}。"""


class QuestionTracker:
    def __init__(self, config, db, llm, on_new_qa=None):
        self.db = db
        self.llm = llm
        self.window = int(config["mining"]["reply_window_minutes"]) * 60
        self.thanks_words = config["mining"]["thanks_words"]
        self.on_new_qa = on_new_qa      # callable(qa_id) 通知审核
        # room -> {"question": {...}, "replies": [(sender, name, text)], "q_ts": int}
        self._active = {}
        self._lock = threading.Lock()

    # ---------- 入口：每条普通群消息 ----------
    def feed(self, msg):
        if not msg.is_group or msg.type != 1:
            return
        with self._lock:
            self._expire_locked(msg.room)
            state = self._active.get(msg.room)
            if state and state["question"]["questioner"] == msg.sender:
                if self._is_thanks(msg.content) and state["replies"]:
                    self._extract_locked(msg.room)
                    return
                # 提问者自己补充说明：追加到问题文本，不动回复
                if not is_question(msg.content):
                    state["question"]["text"] += " " + msg.content
                return
            if is_question(msg.content):
                if state and not state["replies"]:
                    # 之前的问题没人回，直接被新问题替换
                    self.db.set_question_status(state["question"]["id"], "expired")
                qid = self.db.create_question(msg.id, msg.room, msg.sender, msg.content)
                self._active[msg.room] = {
                    "question": {"id": qid, "questioner": msg.sender,
                                 "questioner_name": msg.sender_name, "text": msg.content},
                    "replies": [], "q_ts": msg.ts}
                log.info("新问题 #%d [%s] %s: %s", qid, msg.room, msg.sender, msg.content[:60])
                return
            if state and msg.sender != state["question"]["questioner"]:
                state["replies"].append((msg.sender, msg.sender_name, msg.content))

    # ---------- 管理员直接通过某条问题 ----------
    def approve_question(self, qid):
        """web/命令入口：人工指定某条 open/expired 问题，用其后的群消息直接抽取。"""
        q = None
        for row in self.db.questions_by_status("open") + self.db.questions_by_status("expired"):
            if row["id"] == qid:
                q = row
                break
        if not q:
            return None, f"问题 #{qid} 不存在或非 open/expired"
        since = q["created_at"]
        msgs = self.db.recent_messages(q["room"], since, limit=50)
        replies = [(m["sender_wxid"], m["sender_name"], m["content"])
                   for m in msgs if m["sender_wxid"] not in (q["questioner"],) and m["type"] == 1]
        if not replies:
            return None, f"问题 #{qid} 之后没有找到候选回复"
        qa_id = self._make_pair(q["id"], q["room"], q["text"], replies)
        self.db.set_question_status(qid, "answered")
        return qa_id, f"已从问题 #{qid} 抽取问答对 #{qa_id}，待审核"

    # ---------- 内部 ----------
    def _is_thanks(self, text):
        t = (text or "").strip()
        if not t or len(t) > 30:      # 感谢词很短，长文本大概率是正文
            return False
        return any(w in t.lower() for w in self.thanks_words)

    def _expire_locked(self, room):
        state = self._active.get(room)
        if state and time.time() - state["q_ts"] > self.window * 2:
            self.db.set_question_status(state["question"]["id"], "expired")
            self._active.pop(room, None)

    def _extract_locked(self, room):
        state = self._active.pop(room, None)
        if not state:
            return
        qa_id = self._make_pair(state["question"]["id"], room,
                                state["question"]["text"], state["replies"])
        self.db.set_question_status(state["question"]["id"], "answered")

    def _make_pair(self, question_id, room, q_text, replies):
        answerer = replies[-1][0]
        try:
            if self.llm.ready:
                convo = "\n".join(f"[回复{i+1}] {name or sender}: {text}"
                                  for i, (sender, name, text) in enumerate(replies))
                result = self.llm.chat_json(
                    _EXTRACT_SYSTEM,
                    f"## 问题\n{q_text}\n## 候选回复\n{convo}")
                q, a = (result.get("q") or "").strip(), (result.get("a") or "").strip()
            else:
                q, a = "", ""
        except Exception as e:
            log.error("LLM 抽取问答对失败，回退原文: %s", e)
            q, a = "", ""
        if not q:
            q = q_text                              # 回退：原文即问题
        if not a:
            a = "\n".join(f"{name or sender}: {text}" for sender, name, text in replies)
        qa_id = self.db.create_qa_pair(question_id, room, q, a, answerer, [q_text] + [r[2] for r in replies])
        self.db.audit("tracker", "qa_extracted", f"#{qa_id} from question #{question_id}")
        log.info("抽取问答对 #%d (q=%s...)", qa_id, q[:40])
        if self.on_new_qa:
            try:
                self.on_new_qa(qa_id)
            except Exception as e:
                log.error("on_new_qa 回调失败: %s", e)
        return qa_id
