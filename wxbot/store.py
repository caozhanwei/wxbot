# -*- coding: utf-8 -*-
"""SQLite 存储：运行日志、劝告计数、待沉淀问题、审计。"""
import logging
import sqlite3
import threading
import time

log = logging.getLogger("wxbot.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT,
    chat TEXT, peer TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS warns(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, chat TEXT,
    member TEXT, word TEXT);
CREATE TABLE IF NOT EXISTS qa_pending(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, grp TEXT,
    questioner TEXT, question TEXT, answer TEXT, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT,
    action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS mined(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, grp TEXT,
    question TEXT, answer TEXT);
CREATE TABLE IF NOT EXISTS mined_seq(
    chat TEXT PRIMARY KEY, seq INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS kb_sink(
    chat TEXT PRIMARY KEY, seq INTEGER, ts REAL);
"""


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def log(self, kind, chat="", peer="", detail=""):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO logs(ts,kind,chat,peer,detail) VALUES(?,?,?,?,?)",
                      (time.time(), kind, chat, peer, str(detail)[:500]))

    def audit(self, actor, action, detail=""):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
                      (time.time(), actor, action, str(detail)[:500]))

    # ---------- 劝告 ----------
    def bump_warn(self, chat, member, word):
        """记录一次违禁命中并返回这是第几次。"""
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO warns(ts,chat,member,word) VALUES(?,?,?,?)",
                      (time.time(), chat, member, word))
            row = c.execute("SELECT COUNT(*) n FROM warns WHERE chat=? AND member=?",
                            (chat, member)).fetchone()
            return row["n"]

    def warn_counts(self, chat=None):
        q = ("SELECT chat, member, COUNT(*) n, MAX(ts) last FROM warns "
             + ("WHERE chat=?" if chat else "") + " GROUP BY chat, member ORDER BY last DESC")
        with self._conn() as c:
            rows = c.execute(q, (chat,) if chat else ()).fetchall()
            return [dict(r) for r in rows]

    # ---------- 待沉淀问题 ----------
    def add_qa_pending(self, grp, questioner, question, answer=""):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO qa_pending(ts,grp,questioner,question,answer) "
                      "VALUES(?,?,?,?,?)",
                      (time.time(), grp, questioner, question[:500], answer[:500]))

    def qa_pending(self, limit=50):
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM qa_pending WHERE status='pending' ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---------- 挖掘 ----------
    def add_mined(self, grp, pairs):
        with self._lock, self._conn() as c:
            c.executemany(
                "INSERT INTO mined(ts,grp,question,answer) VALUES(?,?,?,?)",
                [(time.time(), grp, str(q)[:200], str(a)[:400]) for q, a in pairs])

    def mined_pairs(self, grp=None, limit=50):
        q = ("SELECT * FROM mined" + (" WHERE grp=?" if grp else "")
             + " ORDER BY id DESC LIMIT ?")
        with self._conn() as c:
            rows = c.execute(q, ((grp,) if grp else ()) + (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mined_count(self, grp=None):
        q = "SELECT COUNT(*) n FROM mined" + (" WHERE grp=?" if grp else "")
        with self._conn() as c:
            return c.execute(q, ((grp,) if grp else ())).fetchone()["n"]

    def get_mined_seq(self, chat):
        with self._conn() as c:
            row = c.execute("SELECT seq FROM mined_seq WHERE chat=?",
                            (chat,)).fetchone()
            return row["seq"] if row else 0

    def set_mined_seq(self, chat, seq):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO mined_seq(chat,seq,ts) VALUES(?,?,?) "
                      "ON CONFLICT(chat) DO UPDATE SET seq=excluded.seq, ts=excluded.ts",
                      (chat, seq, time.time()))

    # ---------- KB 沉淀水位 ----------
    def get_kb_seq(self, chat):
        with self._conn() as c:
            row = c.execute("SELECT seq FROM kb_sink WHERE chat=?",
                            (chat,)).fetchone()
            return row["seq"] if row else 0

    def set_kb_seq(self, chat, seq):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO kb_sink(chat,seq,ts) VALUES(?,?,?) "
                      "ON CONFLICT(chat) DO UPDATE SET seq=excluded.seq, ts=excluded.ts",
                      (chat, seq, time.time()))

    # ---------- 查询 ----------
    def recent_logs(self, limit=50):
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
