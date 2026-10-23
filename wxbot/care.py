# -*- coding: utf-8 -*-
"""R3 主动关怀：好久没聊的好友生成问候；护栏=默认演练/每人每周1次/日上限/急停。"""
import logging
import time

log = logging.getLogger("wxbot.care")

_DEFAULTS = {"enabled": False, "dry_run": True, "hour": 10,
             "days_min": 7, "max_per_day": 3}


def run_care(bot):
    """每日关怀任务：返回发送（含演练）条数；护栏不过一律跳过。"""
    cfg = (bot.cfg.data.get("app") or {}).get("care") or {}
    if not cfg.get("enabled"):
        return 0
    dry_run = cfg.get("dry_run", True)
    try:
        days_min = int(cfg.get("days_min") or _DEFAULTS["days_min"])
        max_per_day = int(cfg.get("max_per_day") or _DEFAULTS["max_per_day"])
    except (TypeError, ValueError):
        days_min, max_per_day = _DEFAULTS["days_min"], _DEFAULTS["max_per_day"]
    now = time.time()
    sent_today = len([1 for r in _care_all(bot) if now - r["ts"] < 86400])
    n = 0
    for w in list(bot.watch or []):
        if w.get("kind") != "contact" or not (w.get("cfg") or {}).get("enabled", True):
            continue
        if sent_today + n >= max_per_day:
            break
        uid, display = w["user"], w["display"]
        if bot.store.care_count_since(uid, 7):       # 每人每周最多 1 次
            continue
        try:
            rows = bot.driver.db.get_messages(uid, limit=1) or []
        except Exception as e:
            log.warning("读 %s 最近消息失败: %s", display, e)
            continue
        last_ts = max((r.get("create_time") or 0) for r in rows) if rows else 0
        days = (now - last_ts) / 86400 if last_ts else 999
        if days < days_min:
            continue
        memory = ""
        try:
            memory = bot.store.get_contact_memory(uid) or ""
        except Exception:
            memory = ""
        try:
            text = bot.personas.care_greeting(display, memory, int(days))
        except Exception as e:
            log.warning("关怀话术生成失败(%s): %s", display, e)
            continue
        if not text:
            continue
        if dry_run:
            bot.store.log("care_dry", display, uid, text[:80])
            log.info("[演练] 关怀 %s: %s", display, text[:60])
            n += 1
            continue
        ok, why = bot.gate.allow("care", display, text)
        if not ok:
            log.info("关怀被闸门拦截(%s): %s", display, why)
            continue
        if bot.send(display, text, "care"):
            bot.store.log_care(uid, display, text[:80])
            n += 1
    return n


def _care_all(bot):
    """care_log 全量（日上限粗判用，量级极小直接读）。"""
    with bot.store._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM care_log ORDER BY id DESC LIMIT 200").fetchall()]
