# -*- coding: utf-8 -*-
"""R3 约定提醒：私聊里抽取双方约定 → 到期推送主人/群里提醒 → #提醒/#销项。"""
import logging
import time

log = logging.getLogger("wxbot.reminders")

EXTRACT_INTERVAL = 1800      # 约定抽取巡检间隔（秒）
FIRE_INTERVAL = 60           # 到期触发巡检间隔（秒）
_MAX_AHEAD_DAYS = 60         # 只认 60 天内的约定，防 LLM 幻觉远期


def _weekday_cn(dt):
    # time.struct_time：tm_wday 0=周一
    return "星期" + "一二三四五六日"[dt.tm_wday]


def extract_chat(bot, w, rows):
    """水位后的私聊消息 → LLM 抽约定入库；LLM 失败不推水位待重试。"""
    since = bot.store.get_remind_seq(w["user"])
    rows = [r for r in rows or []
            if (r.get("sort_seq") or 0) > since and r.get("type") == "文本"
            and not (r.get("content") or "").startswith("<")][-40:]
    if not rows:
        return 0
    now = time.time()
    latest = max(r.get("create_time") or 0 for r in rows) or now
    base = time.localtime(latest or now)
    lines = []
    for r in rows:
        sender = r.get("sender_username") or ""
        name = bot.driver.display_of(sender) or sender or "?"
        lines.append(f"{name}: {(r.get('content') or '')[:120]}")
    try:
        data = bot.personas.extract_reminders(
            lines, time.strftime("%Y-%m-%d", base) + " " + _weekday_cn(base))
    except Exception as e:
        log.info("约定抽取失败(%s): %s", w["display"], e)
        return 0
    n = 0
    for it in (data or {}).get("items") or []:
        try:
            who = str(it.get("who") or "").strip()[:30]
            what = str(it.get("what") or "").strip()[:80]
            d = str(it.get("date") or "").strip()
            t = str(it.get("time") or "09:00").strip()
            due = time.mktime(time.strptime(f"{d} {t}", "%Y-%m-%d %H:%M"))
        except (AttributeError, ValueError, OverflowError):
            continue
        if not who or not what or due < now or due > now + _MAX_AHEAD_DAYS * 86400:
            continue
        bot.store.add_reminder(w["display"], who, what, due)
        n += 1
    bot.store.set_remind_seq(w["user"], max(r["sort_seq"] for r in rows))
    if n:
        log.info("从 %s 抽到 %d 条约定", w["display"], n)
    return n


def maybe_extract(bot):
    """主循环节流入口：每轮间隔扫一遍启用联系人私聊。"""
    if time.time() - bot.state.get("remind_extract_ts", 0) < EXTRACT_INTERVAL:
        return
    bot.state["remind_extract_ts"] = time.time()
    for w in list(bot.watch or []):
        if w.get("kind") != "contact" or not (w.get("cfg") or {}).get("enabled", True):
            continue
        try:
            rows = bot.driver.db.get_messages(w["user"], limit=60) or []
        except Exception as e:
            log.warning("读 %s 私聊失败: %s", w["display"], e)
            continue
        try:
            extract_chat(bot, w, rows)
        except Exception as e:
            log.warning("约定抽取异常(%s): %s", w["display"], e)


def maybe_fire(bot):
    """主循环节流入口：每分钟检查一次到期约定并推送。"""
    if time.time() - bot.state.get("remind_fire_ts", 0) < FIRE_INTERVAL:
        return
    bot.state["remind_fire_ts"] = time.time()
    fire_due(bot)


def fire_due(bot):
    """到期约定：群里的发群里提醒，私事推文件传输助手；同一约定只发一次。"""
    for r in bot.store.due_reminders(limit=5):
        due_str = time.strftime("%m-%d %H:%M", time.localtime(r["due"]))
        if r.get("grp"):
            chat = r.get("grp_name") or r["grp"]
            text = f"@{r['who_name']} 之前说好的「{r['what']}」到时间啦（{due_str}）"
        else:
            chat = "文件传输助手"
            text = (f"【约定提醒】{r['who_name']}：{r['what']}"
                    f"（约定时间 {due_str}，记自与「{r['src']}」的私聊）")
        ok, why = bot.gate.allow("remind", chat, text)
        if not ok:
            log.info("约定提醒被闸门拦截(%s): %s", chat, why)
            continue
        if bot.send(chat, text, "remind"):
            bot.store.mark_reminder(r["id"], "pending", fired=time.time())


# ---------- 主人命令（对话台/管理员私聊通用） ----------

def cmd_list(bot):
    rows = bot.store.list_reminders(status="pending")
    if not rows:
        return "没有待办的约定，#提醒 就我盯着"
    return "\n".join(f"#{r['id']} {r['who_name']}：{r['what']}"
                     f"（{time.strftime('%m-%d %H:%M', time.localtime(r['due']))}到期）"
                     for r in rows)


def cmd_done(bot, args):
    if not args:
        return "用法：#销项 <编号>（编号看 #提醒）"
    try:
        rid = int(args[0])
    except ValueError:
        return "编号得是数字，看 #提醒"
    for r in bot.store.list_reminders(status="pending"):
        if r["id"] == rid:
            bot.store.mark_reminder(rid, "done")
            return f"已销项：{r['who_name']} 的「{r['what']}」"
    return f"没找到待办约定 #{rid}，看 #提醒"
