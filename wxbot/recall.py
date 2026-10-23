# -*- coding: utf-8 -*-
"""R2 跨聊天问答：主人自然问句 → 关键词检索全部启用聊天 → 带出处 LLM 整合回答。"""
import logging
import re
import time

from wxbot import group_logic

log = logging.getLogger("wxbot.recall")

_FALLBACK_SPLIT = re.compile(r"[\s，。！？、：；,!?~·（）()\[\]【】《》\"'“”‘’@#]+")
_STOPWORDS = {
    "群里", "请问", "一下", "的事", "什么", "怎么", "哪个", "哪里", "大家",
    "知道", "告诉我", "最近", "今天", "昨天", "前天", "帮我", "查查", "查一下",
    "问题", "相关", "记录", "这个", "那个", "有没有", "是不是", "有没有人",
}


def extract_terms(bot, question, default_days=30):
    """问句 → {"keywords", "who", "days"}；LLM 挂了/没配走朴素切词兜底。"""
    try:
        data = bot.personas.extract_recall_terms(question) or {}
        kws = [str(k).strip() for k in (data.get("keywords") or [])
               if k and str(k).strip()]
        if kws:
            try:
                days = int(data.get("days") or default_days)
            except (TypeError, ValueError):
                days = default_days
            return {"keywords": kws[:6], "who": str(data.get("who") or "").strip(),
                    "days": max(1, days)}
    except Exception as e:
        log.info("LLM 关键词抽取失败，走朴素切词: %s", e)
    segs = [s for s in _FALLBACK_SPLIT.split(question or "")
            if len(s) >= 2 and s not in _STOPWORDS and "谁" not in s]
    return {"keywords": segs[:6], "who": "", "days": default_days}


def _fmt_ts(ts):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ""


def search_history(bot, terms, limit_per_chat=800, max_rows=60):
    """按关键词扫全部启用会话（群+联系人+被动私聊，不含主人对话台）。

    过滤：文本类型、非 xml、时间窗内、关键词任一命中；可选说话人。
    排序：命中词数多在前，其次时间新在前；最多 max_rows 行。
    """
    kws = [str(k).lower() for k in (terms.get("keywords") or []) if k]
    who = str(terms.get("who") or "").strip()
    try:
        days = max(1, int(terms.get("days") or 30))
    except (TypeError, ValueError):
        days = 30
    if not kws:
        return []
    cutoff = time.time() - days * 86400
    out = []
    for w in (bot.watch or []):
        if w.get("kind") == "console":
            continue
        try:
            rows = bot.driver.db.get_messages(w["user"], limit=limit_per_chat) or []
        except Exception as e:
            log.warning("读 %s 历史失败: %s", w.get("display") or w["user"], e)
            continue
        is_group = w.get("kind") == "group"
        for r in rows:
            if (r.get("type") or "文本") != "文本":
                continue
            ts = r.get("create_time") or 0
            if ts < cutoff:
                continue
            sender = r.get("sender_username") or ""
            content = r.get("content") or ""
            if is_group:
                _, content = group_logic.parse_group_content(content)
            text = content.strip()
            if not text or text.lstrip().startswith("<"):
                continue
            hits = sum(1 for k in kws if k in text.lower())
            if not hits:
                continue
            display = bot.driver.display_of(sender) or sender
            if who and who not in display and who != sender:
                continue
            out.append({"chat": w.get("display") or w["user"], "user": w["user"],
                        "sender": sender, "sender_display": display,
                        "ts": ts, "content": text, "hits": hits})
    out.sort(key=lambda r: (-r["hits"], -r["ts"]))
    return out[:max_rows]


def answer(bot, question):
    """主人在对话台的自然提问 → 带出处（谁/哪群/大概时间）的整合回答。"""
    terms = extract_terms(bot, question)
    rows = search_history(bot, terms)
    if not rows:
        return "没查到相关记录，换个关键词或说法再试试？"
    lines = [f"【{r['chat']}】{r['sender_display']}({_fmt_ts(r['ts'])}): {r['content'][:80]}"
             for r in rows[:40]]
    try:
        out = bot.personas.chat_recall(question, lines)
        return str(out or "").strip() or ("查到这些记录：\n" + "\n".join(lines[:10]))
    except Exception as e:
        log.warning("LLM 整合回答失败，机械列出兜底: %s", e)
        return "查到这些记录：\n" + "\n".join(lines[:10])
