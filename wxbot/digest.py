# -*- coding: utf-8 -*-
"""R1 感知闭环：群精华日报 + 错过消息补报。

- on_private_msg：非白名单私聊被动监听 → 入 missed_cache；紧急词命中即时推送
- record_at_missed：群内 @我未答（被顶替/过气/暂停）入库
- run_digest：多群总日报（热点 LLM 摘要 + 未解清单 + 找你的事 + 精选发言），
  按群分条过 gate 推送；写 digest_log 水位 / daily_stats / 消化 missed
- report_missed_now：#补报 即时汇总未消化条目
"""
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta

from .gate import Gate

log = logging.getLogger("wxbot.digest")

_URGENT_RE = re.compile(r"急|紧急|尽快|马上|立刻|当天必须|今天必须")
_STOP_WORDS = set("的了呢吧啊吗呀哦哈嘛啦是与在我也你他她就都有不没这个那个什么怎么"
                  "还是如果但是因为所以然后已经可以就是一下他们我们你们".split()
                  + list("~！!？?，,。.、:：;；()（）[]【】"))
_PUSH_DEFAULT = "文件传输助手"
_STATS_MSG_LIMIT = 800


def _today(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d")


def is_urgent(text):
    return bool(_URGENT_RE.search(text or ""))


def _gate(bot):
    g = getattr(bot, "gate", None)
    return g if isinstance(g, Gate) else None


def _push_target(bot):
    cfg = (bot.cfg.data.get("app") or {}).get("daily_digest") or {}
    return str(cfg.get("push_to") or _PUSH_DEFAULT).strip() or _PUSH_DEFAULT


# ---------- 收集 ----------

def on_private_msg(bot, w, m):
    """被动监听的非白名单私聊：入库 missed；紧急即时推（急件豁免时段窗）。"""
    if m.get("is_self") or not m.get("sender"):
        return
    content = str(m.get("content") or "").strip()
    if not content or m.get("type") != "文本" or content.startswith("<?xml"):
        return
    urgent = is_urgent(content)
    bot.store.add_missed("private", w["display"], m.get("sender") or "",
                         m.get("sender_display") or "", content,
                         ts=m.get("ts"), urgency="urgent" if urgent else "normal")
    if urgent:
        urgent_notify(bot, w, m, content)


def urgent_notify(bot, w, m, content):
    text = (f"[急件] {w['display']}: {content[:120]}")
    g = _gate(bot)
    ok, why = g.allow("urgent", _push_target(bot), text, urgent=True) if g \
        else (True, "ok")
    if not ok:
        log.warning("急件被闸门拦截(%s): %s", why, text[:40])
        return
    if bot.send(_push_target(bot), text, "digest"):
        log.info("急件已即时推送 ← %s", w["display"])


def record_at_missed(bot, w, m):
    """群里 @我但机器人没答（被更新的@顶替/过气/全局暂停）→ 补报缓存。"""
    try:
        bot.store.add_missed("at", w["display"], m.get("sender") or "",
                             m.get("sender_display") or "",
                             str(m.get("content") or "")[:300], ts=m.get("ts"))
    except Exception as e:
        log.warning("记录@未答失败(%s): %s", w["display"], e)


# ---------- 日报 ----------

def _top_words(lines, n=6):
    words = Counter()
    for line in lines:
        for seg in re.split(r"[\s：:，,。.！!？?；;、]+", line or ""):
            seg = seg.strip()
            if len(seg) >= 2 and seg not in _STOP_WORDS:
                words[seg] += 1
    return " ".join(w for w, _ in words.most_common(n))


def _group_section(bot, w, day):
    """生成单群小节：返回 (text, msg_count, active, top_words)。"""
    from . import mining
    gid, gname = w["user"], w["display"]
    cutoff = time.time() - 24 * 3600
    rows = bot.driver.db.get_messages(gid, limit=_STATS_MSG_LIMIT) or []
    rows = [r for r in rows
            if (r.get("create_time") or 0) >= cutoff and r.get("sender_username")]
    last_seq = max((r.get("sort_seq") or 0 for r in rows), default=0)
    items = mining._normalize(bot.driver, rows)
    lines = [x["line"] for x in items][-300:]
    speakers = {x.get("sender") or x.get("name") or "" for x in items}
    active = len([s for s in speakers if s])
    top = _top_words(lines)
    hot = ""
    if lines:
        try:
            hot = bot.personas.summarize_day(
                gname, lines,
                "总结过去24小时群里的热点话题与争论点，并列出1~3条值得一看的发言"
                "（注明发言人昵称），口语化简洁")
        except Exception as e:
            log.warning("日报摘要失败(%s): %s", gname, e)
        if not hot:
            try:   # 重试一次
                hot = bot.personas.summarize_day(gname, lines, "总结过去24小时群里的重点")
            except Exception as e:
                log.warning("日报摘要重试仍失败(%s): %s", gname, e)
    if not hot:
        hot = (f"[简版] 近24小时 {len(lines)} 条消息，高频词：{top or '无'}")
    pending = bot.store.pending_issues(grp=gid, limit=5)
    pend_txt = ""
    if pending:
        pend_txt = "\n未解问题：\n" + "\n".join(
            f"· {p.get('asker') or '有人'}：{(p.get('question') or '')[:40]}"
            for p in pending)
    missed = [r for r in bot.store.missed_unconsumed(limit=200)
              if r.get("src") == "at" and r.get("chat") == gname]
    miss_txt = ""
    if missed:
        miss_txt = "\n找你的事（@我未答）：\n" + "\n".join(
            f"· {r.get('sender_display') or r.get('sender')}：{(r.get('content') or '')[:40]}"
            for r in missed[:8])
    text = (f"【{gname}】\n{hot}{pend_txt}{miss_txt}")
    return text, len(lines), active, top, last_seq


def _private_section(bot):
    missed = [r for r in bot.store.missed_unconsumed(limit=200)
              if r.get("src") == "private"]
    if not missed:
        return ""
    by_chat = {}
    for r in missed:
        by_chat.setdefault(r.get("chat") or "?", []).append(r)
    lines = [f"· {chat}（{len(rs)}条）：{rs[-1].get('content') or ''}"
             for chat, rs in list(by_chat.items())[:8]]
    return "\n\n【找你的事（私聊）】\n" + "\n".join(lines)


def run_digest(bot):
    """多群总日报：逐群小节 + 私聊找你的事，按群分条过闸推送。"""
    cfg = (bot.cfg.data.get("app") or {}).get("daily_digest") or {}
    if not cfg.get("enabled"):
        return
    day = _today()
    push_to = _push_target(bot)
    groups = [w for w in bot.watch
              if w.get("kind") == "group"
              and (w["cfg"].get("digest") or {}).get("enabled")]
    if not groups and not bot.store.missed_unconsumed():
        return
    consumed = []
    owner_header_done = False
    for w in groups:
        if bot.store.digest_done_day(w["user"], day):
            continue                     # 当天已出过（补跑幂等）
        try:
            text, n_msg, active, top, last_seq = _group_section(bot, w, day)
            bot.store.upsert_daily_stats(
                day, w["user"], msg_count=n_msg, active_members=active,
                top_words=top,
                pending_count=bot.store.pending_issues_count(grp=w["user"]))
            to_group = bool((w["cfg"].get("digest") or {}).get("to_group"))
            if to_group:                 # 群回顾直接发进群聊（群友可见）
                target = w["display"]
                body = (f"📋 {day} 群回顾\n"
                        + (text.split("\n", 1)[1] if text.startswith("【") else text))
            else:
                target = push_to
                body = text
                if not owner_header_done:
                    body = f"📋 {day} 群日报\n" + text
                    owner_header_done = True
            g = _gate(bot)
            ok, why = g.allow("digest", target, body) if g else (True, "ok")
            if ok:
                if not bot.send(target, body, "digest"):
                    bot.notify_admins(f"[告警] 日报推送到 {target} 失败（{w['display']} 小节）")
            else:
                log.warning("日报小节被闸门拦截(%s): %s", w["display"], why)
                bot.store.digest_done(w["user"], day, last_seq)
                continue
            bot.store.digest_done(w["user"], day, last_seq)
            consumed += [r["id"] for r in bot.store.missed_unconsumed(limit=200)
                         if r.get("src") == "at" and r.get("chat") == w["display"]]
        except Exception as e:
            log.exception("日报群小节失败(%s): %s", w["display"], e)
    priv = _private_section(bot)
    if priv:
        g = _gate(bot)
        ok, why = g.allow("digest", push_to, priv) if g else (True, "ok")
        if ok and bot.send(push_to, priv, "digest"):
            consumed += [r["id"] for r in bot.store.missed_unconsumed(limit=200)
                         if r.get("src") == "private"]
    bot.store.mark_missed_consumed(consumed)
    log.info("日报完成：%d 群，消化补报 %d 条", len(groups), len(consumed))


# ---------- 周报 ----------

_WEEK_MSG_LIMIT = 3000


def _weekly_cfg(bot):
    return (bot.cfg.data.get("app") or {}).get("weekly_report") or {}


def run_weekly(bot):
    """群活跃周报（app.weekly_report，默认关）：近 7 天每群 发言量/活跃人数/
    本周沉淀/待解决 + LLM 热点摘要，合成一条总报推给 push_to。
    只在配置的 weekday（0=周一，date.weekday() 语义）当天跑，其余天静默。"""
    cfg = _weekly_cfg(bot)
    if not cfg.get("enabled"):
        return
    now = time.time()
    today = datetime.fromtimestamp(now).date()
    try:
        wd = int(cfg.get("weekday", 0) or 0)
    except (TypeError, ValueError):
        wd = 0
    if today.weekday() != wd:
        return
    from . import mining
    push_to = str(cfg.get("push_to") or "").strip() or _push_target(bot)
    groups = [w for w in bot.watch if w.get("kind") == "group"]
    cutoff = now - 7 * 24 * 3600
    secs, total_msg, total_act = [], 0, 0
    for w in groups:
        gid, gname = w["user"], w["display"]
        try:
            rows = bot.driver.db.get_messages(gid, limit=_WEEK_MSG_LIMIT) or []
        except Exception as e:
            log.warning("周报取数失败(%s): %s", gname, e)
            rows = []
        rows = [r for r in rows
                if (r.get("create_time") or 0) >= cutoff and r.get("sender_username")]
        if not rows:
            continue
        items = mining._normalize(bot.driver, rows)
        lines = [x["line"] for x in items][-500:]
        speakers = {x.get("sender") or x.get("name") or "" for x in items}
        active = len([s for s in speakers if s])
        hot = ""
        try:
            hot = bot.personas.summarize_day(
                gname, lines,
                "总结过去7天群里的热点话题与重要进展，两三句话，口语化简洁")
        except Exception as e:
            log.warning("周报摘要失败(%s): %s", gname, e)
        if not hot:
            hot = f"[简版] 高频词：{_top_words(lines) or '无'}"
        try:
            mined_n = bot.store.mined_count_since(gid, cutoff)
        except Exception:
            mined_n = 0
        try:
            pend_n = bot.store.pending_issues_count(grp=gid)
        except Exception:
            pend_n = 0
        secs.append(f"【{gname}】发言 {len(lines)} 条 · 活跃 {active} 人 · "
                    f"本周沉淀 {mined_n} 问 · 待解决 {pend_n}\n{hot}")
        total_msg += len(lines)
        total_act += active
    if not secs:
        return
    rng = (f"{(today - timedelta(days=6)).strftime('%m-%d')} ~ "
           f"{today.strftime('%m-%d')}")
    body = (f"📊 群活跃周报 {rng}\n（{len(secs)} 个群 · 共 {total_msg} 条发言 · "
            f"{total_act} 人次活跃）\n\n" + "\n\n".join(secs))
    g = _gate(bot)
    ok, why = g.allow("weekly", push_to, body) if g else (True, "ok")
    if not ok:
        log.warning("周报被闸门拦截: %s", why)
        return
    if bot.send(push_to, body, "digest"):
        log.info("周报已推送 → %s", push_to)


def report_missed_now(bot):
    """#补报：即时汇总未消化条目（不清空缓存，日报统一消化）。"""
    rows = bot.store.missed_unconsumed(limit=50)
    if not rows:
        return "没有攒下的漏看消息，都处理完啦"
    at = [r for r in rows if r.get("src") == "at"]
    priv = [r for r in rows if r.get("src") == "private"]
    out = []
    if at:
        out.append("群里@我未答：\n" + "\n".join(
            f"· [{r.get('chat')}] {r.get('sender_display') or r.get('sender')}："
            f"{(r.get('content') or '')[:40]}" for r in at[:15]))
    if priv:
        by_chat = {}
        for r in priv:
            by_chat.setdefault(r.get("chat") or "?", []).append(r)
        out.append("私聊找你：\n" + "\n".join(
            f"· [{chat}]（{len(rs)}条）{(rs[-1].get('content') or '')[:40]}"
            for chat, rs in list(by_chat.items())[:8]))
    out.append(f"共 {len(rows)} 条（晚上日报会自动汇总消化）")
    return "\n\n".join(out)


# ---------- 吐槽收集与每日复盘 ----------

def _complaint_cfg(bot):
    return (bot.cfg.data.get("app") or {}).get("complaint_review") or {}


def run_complaint_review(bot):
    """每日吐槽复盘：LLM 分析近 24h 对机器人的吐槽，归纳共性 + 给优化建议。

    app.complaint_review{enabled,hour=21,push_to}；LLM 挂了降级高频词
    简版；推送成功才消化缓存（失败留待下次）。
    """
    cfg = _complaint_cfg(bot)
    if not cfg.get("enabled"):
        return
    push_to = str(cfg.get("push_to") or "").strip() or _push_target(bot)
    rows = bot.store.complaints_unconsumed(since=time.time() - 24 * 3600,
                                           limit=300)
    if not rows:
        return
    lines = [f"[{r.get('chat')}] {r.get('sender_display') or r.get('sender')}: "
             f"{(r.get('content') or '')[:80]}" for r in rows]
    summary = ""
    try:
        summary = bot.personas.summarize_day(
            "机器人吐槽", lines,
            "以下是用户对微信机器人（群答疑/免@接话/私聊回复）的吐槽原声。"
            "请：1) 归纳 2~4 类共性问题（按严重度排序）；2) 每类给一条具体"
            "可落地的优化建议。口语化简洁，总共 300 字内")
    except Exception as e:
        log.warning("吐槽复盘摘要失败: %s", e)
    if not summary:
        cnt = Counter()
        for r in rows:
            for w in (r.get("marks") or "").split("、"):
                if w:
                    cnt[w] += 1
        top = "、".join(f"{w}×{n}" for w, n in cnt.most_common(6))
        summary = f"[简版] 近 24h 吐槽 {len(rows)} 条，高频词：{top or '无'}"
    quotes = "\n".join(f"· {l[:90]}" for l in lines[:5])
    day = _today()
    body = (f"🧾 机器人吐槽复盘（{day}，近24h {len(rows)} 条）\n{summary}\n\n"
            f"原声摘录：\n{quotes}")
    g = _gate(bot)
    ok, why = g.allow("complaint", push_to, body) if g else (True, "ok")
    if not ok:
        log.warning("吐槽复盘被闸门拦截: %s", why)
        return
    if bot.send(push_to, body, "digest"):
        bot.store.mark_complaints_consumed([r["id"] for r in rows])
        log.info("吐槽复盘已推送：%d 条", len(rows))


def report_complaints_now(bot):
    """#吐槽：即时汇总未消化吐槽（不清空缓存，每晚自动分析消化）。"""
    rows = bot.store.complaints_unconsumed(limit=50)
    if not rows:
        return "还没收集到对机器人的吐槽"
    out = ["近期吐槽原声："] + [
        f"· [{r.get('chat')}] {r.get('sender_display') or r.get('sender')}："
        f"{(r.get('content') or '')[:40]}" for r in rows[:15]]
    out.append(f"共 {len(rows)} 条（每晚自动分析总结并给优化建议）")
    return "\n".join(out)
