# -*- coding: utf-8 -*-
"""R2 群精华主题库：挖掘 QA 聚类成主题 + 提问本地先撞（不问 LLM）。"""
import logging
import re
import time

log = logging.getLogger("wxbot.themes")

_SEG = re.compile(r"[一-鿿A-Za-z0-9]+")


def q_bigrams(text):
    """问题文本的字符二元组集合（英文按小写），供宽松中文匹配。"""
    grams = set()
    for seg in _SEG.findall(text or ""):
        s = seg.lower()
        for i in range(len(s) - 1):
            grams.add(s[i:i + 2])
    return grams


def local_hit(pairs, question, min_shared=3):
    """在挖掘 QA 对里找与提问共享二元组最多且达阈值的一条。

    pairs 元素为 (question, answer) 或 {"question","answer"}；
    命中返回 (question, answer)，否则 None。阈值保守，宁缺勿滥。
    """
    qg = q_bigrams(question)
    if not qg:
        return None
    best, best_n = None, 0
    for it in pairs or []:
        if isinstance(it, dict):
            q, a = it.get("question") or "", it.get("answer") or ""
        else:
            q, a = it[0], it[1]
        shared = len(qg & q_bigrams(q))
        if shared >= min_shared and shared > best_n:
            best, best_n = (q, a), shared
    return best


def assign(bot, grp, batch=20):
    """把该群未归簇的挖掘 QA 批量交给 LLM 聚类写回主题，返回归簇条数。

    LLM 失败返回 0，未归簇数据原样保留待下次重试。
    """
    rows = bot.store.mined_unthemed(grp, batch)
    if not rows:
        return 0
    existing = [t["theme"] for t in (bot.store.themes_list(grp) or [])]
    try:
        data = bot.personas.assign_themes(existing,
                                          [(r["id"], r["question"]) for r in rows])
    except Exception as e:
        log.warning("主题聚类失败(%s): %s", grp, e)
        return 0
    n = 0
    for it in (data or {}).get("items") or []:
        try:
            qa_id = int(it.get("id"))
            theme = str(it.get("theme") or "").strip()[:8]
        except (AttributeError, TypeError, ValueError):
            continue
        if qa_id and theme:
            bot.store.set_theme(qa_id, theme)
            n += 1
    if n:
        log.info("主题归簇 %s: %d 条", grp, n)
    return n


def assign_async(bot, grp):
    """mining 收尾钩子：后台线程跑归簇，绝不让挖掘线程等 LLM。"""
    import threading

    def _run():
        try:
            assign(bot, grp)
        except Exception as e:
            log.warning("后台主题归簇异常(%s): %s", grp, e)

    threading.Thread(target=_run, daemon=True,
                     name=f"themes-{grp}").start()
    return time.time()
