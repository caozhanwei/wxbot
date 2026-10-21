# -*- coding: utf-8 -*-
"""SQLite 存储层：消息、问题、问答对、审计日志、RAGFlow session 缓存。"""
import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    room TEXT NOT NULL,
    sender_wxid TEXT NOT NULL,
    sender_name TEXT DEFAULT '',
    type INTEGER NOT NULL,
    content TEXT DEFAULT '',
    is_at_bot INTEGER DEFAULT 0,
    raw_xml TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_msg_room_ts ON messages(room, ts);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id INTEGER NOT NULL,
    room TEXT NOT NULL,
    questioner TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open/answered/expired
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_q_room_status ON questions(room, status);

CREATE TABLE IF NOT EXISTS qa_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER,
    room TEXT NOT NULL,
    q_text TEXT NOT NULL,
    a_text TEXT NOT NULL,
    answerer_wxid TEXT DEFAULT '',
    source_msgs TEXT DEFAULT '[]',         -- json: 参与抽取的原始消息
    status TEXT NOT NULL DEFAULT 'pending',-- pending/approved/rejected/ingested
    reviewed_by TEXT DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sessions_cache (
    room TEXT PRIMARY KEY,
    ragflow_session_id TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: str = "assistant.db"):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(_SCHEMA)
            self.conn.commit()

    def _exec(self, sql, params=()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    # ---- messages ----
    def insert_message(self, ts, room, sender_wxid, sender_name, mtype, content, is_at_bot, raw_xml=""):
        cur = self._exec(
            "INSERT INTO messages(ts, room, sender_wxid, sender_name, type, content, is_at_bot, raw_xml) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ts, room, sender_wxid, sender_name, mtype, content, int(is_at_bot), raw_xml))
        return cur.lastrowid

    def recent_messages(self, room, since_ts, limit=50):
        cur = self.conn.execute(
            "SELECT * FROM messages WHERE room=? AND ts>=? ORDER BY ts ASC LIMIT ?",
            (room, since_ts, limit))
        return [dict(r) for r in cur.fetchall()]

    # ---- questions ----
    def open_question(self, room):
        cur = self.conn.execute(
            "SELECT * FROM questions WHERE room=? AND status='open' ORDER BY id DESC LIMIT 1", (room,))
        row = cur.fetchone()
        return dict(row) if row else None

    def create_question(self, msg_id, room, questioner, text):
        cur = self._exec(
            "INSERT INTO questions(msg_id, room, questioner, text, status, created_at) "
            "VALUES(?,?,?,?,'open',?)", (msg_id, room, questioner, text, int(time.time())))
        return cur.lastrowid

    def set_question_status(self, qid, status):
        self._exec("UPDATE questions SET status=? WHERE id=?", (status, qid))

    def questions_by_status(self, status, limit=20):
        cur = self.conn.execute(
            "SELECT * FROM questions WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return [dict(r) for r in cur.fetchall()]

    # ---- qa_pairs ----
    def create_qa_pair(self, question_id, room, q_text, a_text, answerer_wxid, source_msgs):
        cur = self._exec(
            "INSERT INTO qa_pairs(question_id, room, q_text, a_text, answerer_wxid, source_msgs, status, created_at) "
            "VALUES(?,?,?,?,?,?,'pending',?)",
            (question_id, room, q_text, a_text, answerer_wxid,
             json.dumps(source_msgs, ensure_ascii=False), int(time.time())))
        return cur.lastrowid

    def qa_pair(self, qid):
        cur = self.conn.execute("SELECT * FROM qa_pairs WHERE id=?", (qid,))
        row = cur.fetchone()
        return dict(row) if row else None

    def qa_pairs_by_status(self, status, limit=100):
        cur = self.conn.execute(
            "SELECT * FROM qa_pairs WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return [dict(r) for r in cur.fetchall()]

    def set_qa_status(self, qid, status, reviewed_by=""):
        self._exec("UPDATE qa_pairs SET status=?, reviewed_by=? WHERE id=?", (status, reviewed_by, qid))

    # ---- audit ----
    def audit(self, actor, action, detail=""):
        self._exec("INSERT INTO audit_log(ts, actor, action, detail) VALUES(?,?,?,?)",
                   (int(time.time()), actor, action, detail))

    # ---- ragflow sessions ----
    def get_session(self, room):
        cur = self.conn.execute("SELECT ragflow_session_id FROM sessions_cache WHERE room=?", (room,))
        row = cur.fetchone()
        return row["ragflow_session_id"] if row else None

    def set_session(self, room, session_id):
        self._exec("INSERT OR REPLACE INTO sessions_cache(room, ragflow_session_id) VALUES(?,?)",
                   (room, session_id))

    # ---- stats ----
    def stats_today(self):
        day_start = int(time.time()) - 24 * 3600
        msgs = self.conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE ts>=?", (day_start,)).fetchone()["c"]
        qs = self.conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE created_at>=?", (day_start,)).fetchone()["c"]
        pending = self.conn.execute(
            "SELECT COUNT(*) c FROM qa_pairs WHERE status='pending'").fetchone()["c"]
        open_q = self.conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE status='open'").fetchone()["c"]
        return {"msgs_24h": msgs, "new_questions_24h": qs, "open_questions": open_q, "pending_qa": pending}


def open_db(path=None) -> DB:
    if path is None:
        path = Path(__file__).parent / "assistant.db"
    return DB(str(path))
