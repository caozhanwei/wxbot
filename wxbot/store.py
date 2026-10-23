# -*- coding: utf-8 -*-
"""SQLite 存储：运行日志、劝告计数、待沉淀问题、审计。"""
import datetime as _dt
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
CREATE TABLE IF NOT EXISTS member_profiles(
    grp TEXT, wxid TEXT, nickname TEXT, profile TEXT,
    updated REAL, PRIMARY KEY(grp, wxid));
CREATE TABLE IF NOT EXISTS contact_memories(
    wxid TEXT PRIMARY KEY, memory TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS pending_issues(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, grp TEXT,
    asker TEXT, question TEXT, context TEXT, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS group_tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT, grp_name TEXT,
    freq TEXT DEFAULT 'daily', hour INTEGER, minute INTEGER,
    instruction TEXT, enabled INTEGER DEFAULT 1,
    created_by TEXT, created_by_name TEXT, created_ts REAL,
    last_run REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS digest_log(
    day TEXT, grp TEXT, last_seq INTEGER, created REAL,
    PRIMARY KEY(day, grp));
CREATE TABLE IF NOT EXISTS missed_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, src TEXT,
    chat TEXT, sender TEXT, sender_display TEXT, content TEXT,
    urgency TEXT DEFAULT 'normal', digested INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS daily_runs(
    job TEXT, day TEXT, ts REAL, PRIMARY KEY(job, day));
CREATE TABLE IF NOT EXISTS daily_stats(
    day TEXT, grp TEXT, msg_count INTEGER, active_members INTEGER,
    top_words TEXT, pending_count INTEGER, updated REAL,
    PRIMARY KEY(day, grp));
CREATE TABLE IF NOT EXISTS mined_themes(
    qa_id INTEGER PRIMARY KEY, theme TEXT, updated REAL);
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

    def mined_pairs(self, grp=None, limit=50, offset=0, theme=None):
        cond, args = [], []
        if grp:
            cond.append("m.grp=?")
            args.append(grp)
        if theme == "__none__":
            cond.append("t.theme IS NULL")
        elif theme:
            cond.append("t.theme=?")
            args.append(theme)
        q = ("SELECT m.*, t.theme theme FROM mined m"
             " LEFT JOIN mined_themes t ON t.qa_id=m.id"
             + (" WHERE " + " AND ".join(cond) if cond else "")
             + " ORDER BY m.id DESC LIMIT ? OFFSET ?")
        with self._conn() as c:
            rows = c.execute(q, tuple(args) + (limit, offset)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["q"] = d.get("question") or ""
                d["a"] = d.get("answer") or ""
                out.append(d)
            return out

    def mined_count(self, grp=None, theme=None):
        cond, args = [], []
        if grp:
            cond.append("m.grp=?")
            args.append(grp)
        if theme == "__none__":
            cond.append("t.theme IS NULL")
        elif theme:
            cond.append("t.theme=?")
            args.append(theme)
        q = ("SELECT COUNT(*) n FROM mined m"
             " LEFT JOIN mined_themes t ON t.qa_id=m.id"
             + (" WHERE " + " AND ".join(cond) if cond else ""))
        with self._conn() as c:
            return c.execute(q, tuple(args)).fetchone()["n"]

    def set_theme(self, qa_id, theme):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO mined_themes(qa_id,theme,updated) VALUES(?,?,?) "
                "ON CONFLICT(qa_id) DO UPDATE SET theme=excluded.theme, "
                "updated=excluded.updated",
                (int(qa_id), str(theme)[:60], time.time()))

    def themes_list(self, grp=None):
        q = ("SELECT t.theme theme, COUNT(*) n FROM mined_themes t"
             " JOIN mined m ON m.id=t.qa_id"
             + (" WHERE m.grp=?" if grp else "")
             + " GROUP BY t.theme ORDER BY n DESC, t.theme")
        with self._conn() as c:
            return [dict(r) for r in
                    c.execute(q, ((grp,) if grp else ())).fetchall()]

    def mined_unthemed(self, grp, limit=20):
        q = ("SELECT m.* FROM mined m LEFT JOIN mined_themes t ON t.qa_id=m.id"
             " WHERE t.qa_id IS NULL AND m.grp=? ORDER BY m.id LIMIT ?")
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, (grp, limit)).fetchall()]

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

    # ---------- 私聊联系人记忆 ----------
    def get_contact_memory(self, wxid):
        with self._conn() as c:
            row = c.execute("SELECT memory FROM contact_memories WHERE wxid=?", (wxid,)).fetchone()
            return row["memory"] if row and row["memory"] else ""

    def upsert_contact_memory(self, wxid, memory):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO contact_memories(wxid, memory, updated) VALUES(?,?,?) "
                "ON CONFLICT(wxid) DO UPDATE SET memory=excluded.memory, updated=excluded.updated",
                (wxid, str(memory)[:2000], time.time()))

    # ---------- 待解决诉求/历史遗留问题 ----------
    def add_pending_issue(self, grp, asker, question, context=""):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO pending_issues(ts, grp, asker, question, context) VALUES(?,?,?,?,?)",
                (time.time(), grp, asker, str(question)[:500], str(context)[:500]))

    def pending_issues(self, grp=None, limit=100, offset=0):
        q = ("SELECT * FROM pending_issues" + (" WHERE grp=?" if grp else "")
             + " ORDER BY id DESC LIMIT ? OFFSET ?")
        with self._conn() as c:
            rows = c.execute(q, ((grp,) if grp else ()) + (limit, offset)).fetchall()
            return [dict(r) for r in rows]

    def pending_issues_count(self, grp=None):
        q = "SELECT COUNT(*) n FROM pending_issues" + (" WHERE grp=?" if grp else "")
        with self._conn() as c:
            return c.execute(q, ((grp,) if grp else ())).fetchone()["n"]

    # ---------- 群定时任务（群主/管理员 @机器人 固化指令） ----------
    def add_task(self, grp, grp_name, hour, minute, instruction,
                 created_by="", created_by_name="", freq="daily"):
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO group_tasks(grp, grp_name, freq, hour, minute, "
                "instruction, created_by, created_by_name, created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (grp, grp_name, freq, int(hour), int(minute),
                 str(instruction)[:500], created_by, created_by_name, time.time()))
            return cur.lastrowid

    def list_tasks(self, grp=None):
        q = ("SELECT * FROM group_tasks WHERE enabled=1"
             + (" AND grp=?" if grp else "") + " ORDER BY id")
        with self._conn() as c:
            rows = c.execute(q, ((grp,) if grp else ())).fetchall()
            return [dict(r) for r in rows]

    def remove_task(self, tid):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM group_tasks WHERE id=?", (tid,))

    def mark_run(self, tid, ts=None):
        with self._lock, self._conn() as c:
            c.execute("UPDATE group_tasks SET last_run=? WHERE id=?",
                      (time.time() if ts is None else ts, tid))

    def due_tasks(self, now=None):
        """已到今天定时点且当天尚未运行的任务。

        每日任务以「当天 HH:MM 槽」为基准：now 早于槽点不触发；过了槽点且
        last_run 还停在槽点之前、且任务创建早于槽点（创建当天 22:00 的 21:00
        任务等到明天才首发）→ 到期，当天只跑一次。
        """
        now = time.time() if now is None else now
        dt = _dt.datetime.fromtimestamp(now)
        out = []
        for t in self.list_tasks():
            hour, minute = int(t.get("hour") or 0), int(t.get("minute") or 0)
            slot = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if slot.timestamp() > now:
                continue                     # 今天的点还没到
            if float(t.get("last_run") or 0) >= slot.timestamp():
                continue                     # 槽点后已跑过
            if float(t.get("created_ts") or 0) >= slot.timestamp():
                continue                     # 槽点后才创建的，明天首发
            out.append(t)
        return out

    # ---------- 错过补报 / 日报 / 日任务 / 每日统计 ----------
    def add_missed(self, src, chat, sender, sender_display, content, ts=None,
                   urgency="normal"):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO missed_cache(ts,src,chat,sender,sender_display,"
                "content,urgency,digested) VALUES(?,?,?,?,?,?,?,0)",
                (time.time() if ts is None else ts, src, chat, sender,
                 sender_display or "", str(content)[:500], urgency))

    def missed_unconsumed(self, src=None, limit=200):
        q = ("SELECT * FROM missed_cache WHERE digested=0"
             + (" AND src=?" if src else "") + " ORDER BY id LIMIT ?")
        with self._conn() as c:
            rows = c.execute(q, ((src,) if src else ()) + (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_missed_consumed(self, ids):
        ids = [int(i) for i in (ids or []) if int(i) > 0]
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE missed_cache SET digested=1 WHERE id IN ({marks})", ids)

    def digest_done(self, grp, day, last_seq=0):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO digest_log(day,grp,last_seq,created) VALUES(?,?,?,?) "
                "ON CONFLICT(day,grp) DO UPDATE SET last_seq=excluded.last_seq, "
                "created=excluded.created",
                (day, grp, int(last_seq), time.time()))

    def digest_done_day(self, grp, day):
        with self._conn() as c:
            return c.execute("SELECT 1 FROM digest_log WHERE day=? AND grp=?",
                             (day, grp)).fetchone() is not None

    def mark_daily_run(self, job, day, ts=None):
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO daily_runs(job,day,ts) VALUES(?,?,?)",
                      (job, day, time.time() if ts is None else ts))

    def daily_ran(self, job, day):
        with self._conn() as c:
            return c.execute("SELECT 1 FROM daily_runs WHERE job=? AND day=?",
                             (job, day)).fetchone() is not None

    def upsert_daily_stats(self, day, grp, msg_count=0, active_members=0,
                           top_words="", pending_count=0):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO daily_stats(day,grp,msg_count,active_members,"
                "top_words,pending_count,updated) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(day,grp) DO UPDATE SET msg_count=excluded.msg_count, "
                "active_members=excluded.active_members, top_words=excluded.top_words, "
                "pending_count=excluded.pending_count, updated=excluded.updated",
                (day, grp, int(msg_count), int(active_members),
                 str(top_words or "")[:200], int(pending_count), time.time()))

    def daily_stats_list(self, day):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM daily_stats WHERE day=? ORDER BY grp",
                             (day,)).fetchall()
            return [dict(r) for r in rows]

    # ---------- 成员画像 ----------
    def get_profile(self, grp, wxid):
        with self._conn() as c:
            row = c.execute("SELECT * FROM member_profiles WHERE grp=? AND wxid=?",
                            (grp, wxid)).fetchone()
            return dict(row) if row else None
    def upsert_profile(self, grp, wxid, nickname, profile):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO member_profiles(grp,wxid,nickname,profile,updated) "
                "VALUES(?,?,?,?,?) ON CONFLICT(grp,wxid) DO UPDATE SET "
                "nickname=excluded.nickname, profile=excluded.profile, "
                "updated=excluded.updated",
                (grp, wxid, nickname, str(profile)[:2000], time.time()))

    def profiles(self, grp=None, limit=200, offset=0):
        q = ("SELECT * FROM member_profiles" + (" WHERE grp=?" if grp else "")
             + " ORDER BY updated DESC LIMIT ? OFFSET ?")
        with self._conn() as c:
            rows = c.execute(q, ((grp,) if grp else ()) + (limit, offset)).fetchall()
            return [dict(r) for r in rows]

    def profiles_count(self, grp=None):
        q = "SELECT COUNT(*) n FROM member_profiles" + (" WHERE grp=?" if grp else "")
        with self._conn() as c:
            return c.execute(q, ((grp,) if grp else ())).fetchone()["n"]

    # ---------- 查询 ----------
    def recent_logs(self, limit=50):
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---------- 知识资产导入导出（跨账号迁移 / 全量备份） ----------
    def export_knowledge(self, source_wxid="", config_yaml=""):
        """导出群友画像+私聊记忆+问答沉淀+待沉淀问题+未解诉求；
        config_yaml 非空时附配置原文（全量备份用，含密钥）。"""
        with self._conn() as c:
            prof = [dict(r) for r in c.execute(
                "SELECT grp,wxid,nickname,profile FROM member_profiles "
                "ORDER BY grp,wxid")]
            mem = [dict(r) for r in c.execute(
                "SELECT wxid,memory FROM contact_memories ORDER BY wxid")]
            qa = [dict(r) for r in c.execute(
                "SELECT grp,question,answer FROM mined ORDER BY id")]
            qap = [dict(r) for r in c.execute(
                "SELECT grp,questioner,question,answer,status FROM qa_pending "
                "ORDER BY id")]
            iss = [dict(r) for r in c.execute(
                "SELECT grp,asker,question,context,status FROM pending_issues "
                "ORDER BY id")]
        data = {"wxbot_export": 2, "exported_at": time.time(),
                "source_wxid": source_wxid,
                "member_profiles": prof, "contact_memories": mem,
                "mined_qa": qa, "qa_pending": qap, "pending_issues": iss}
        if config_yaml:
            data["config_yaml"] = config_yaml
        return data

    def import_knowledge(self, data, overwrite=False):
        """导入 export_knowledge 产物；同键已有数据时 overwrite=True 才覆盖。
        返回各资产实际写入条数（v1 老包缺的段计 0）。"""
        n = {"profiles": 0, "memories": 0, "mined_qa": 0,
             "qa_pending": 0, "pending_issues": 0}
        if not isinstance(data, dict):
            return n
        with self._lock, self._conn() as c:
            for p in data.get("member_profiles") or []:
                if not isinstance(p, dict) or not (p.get("grp") and p.get("wxid")):
                    continue
                if not overwrite and c.execute(
                        "SELECT 1 FROM member_profiles WHERE grp=? AND wxid=?",
                        (p["grp"], p["wxid"])).fetchone():
                    continue
                c.execute(
                    "INSERT INTO member_profiles(grp,wxid,nickname,profile,updated) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(grp,wxid) DO UPDATE SET "
                    "nickname=excluded.nickname, profile=excluded.profile, "
                    "updated=excluded.updated",
                    (p["grp"], p["wxid"], str(p.get("nickname") or "")[:100],
                     str(p.get("profile") or "")[:2000], time.time()))
                n["profiles"] += 1
            for m in data.get("contact_memories") or []:
                if not isinstance(m, dict) or not m.get("wxid"):
                    continue
                if not overwrite and c.execute(
                        "SELECT 1 FROM contact_memories WHERE wxid=?",
                        (m["wxid"],)).fetchone():
                    continue
                c.execute(
                    "INSERT INTO contact_memories(wxid,memory,updated) VALUES(?,?,?) "
                    "ON CONFLICT(wxid) DO UPDATE SET memory=excluded.memory, "
                    "updated=excluded.updated",
                    (m["wxid"], str(m.get("memory") or "")[:2000], time.time()))
                n["memories"] += 1
            for q in data.get("mined_qa") or []:
                if not isinstance(q, dict) or not q.get("question"):
                    continue
                if c.execute("SELECT 1 FROM mined WHERE grp=? AND question=?",
                             ((q.get("grp") or ""), str(q["question"])[:200])).fetchone():
                    continue
                c.execute("INSERT INTO mined(ts,grp,question,answer) VALUES(?,?,?,?)",
                          (time.time(), q.get("grp") or "",
                           str(q["question"])[:200], str(q.get("answer") or "")[:400]))
                n["mined_qa"] += 1
            for q in data.get("qa_pending") or []:
                if not isinstance(q, dict) or not q.get("question"):
                    continue
                if c.execute(
                        "SELECT 1 FROM qa_pending WHERE grp=? AND questioner=? "
                        "AND question=?",
                        (q.get("grp") or "", q.get("questioner") or "",
                         str(q["question"])[:500])).fetchone():
                    continue
                c.execute(
                    "INSERT INTO qa_pending(ts,grp,questioner,question,answer,status) "
                    "VALUES(?,?,?,?,?,?)",
                    (time.time(), q.get("grp") or "", q.get("questioner") or "",
                     str(q["question"])[:500], str(q.get("answer") or "")[:500],
                     q.get("status") or "pending"))
                n["qa_pending"] += 1
            for it in data.get("pending_issues") or []:
                if not isinstance(it, dict) or not it.get("question"):
                    continue
                if c.execute(
                        "SELECT 1 FROM pending_issues WHERE grp=? AND asker=? "
                        "AND question=?",
                        (it.get("grp") or "", it.get("asker") or "",
                         str(it["question"])[:500])).fetchone():
                    continue
                c.execute(
                    "INSERT INTO pending_issues(ts,grp,asker,question,context,status) "
                    "VALUES(?,?,?,?,?,?)",
                    (time.time(), it.get("grp") or "", it.get("asker") or "",
                     str(it["question"])[:500], str(it.get("context") or "")[:500],
                     it.get("status") or "pending"))
                n["pending_issues"] += 1
        return n
