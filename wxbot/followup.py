# -*- coding: utf-8 -*-
"""R3 未解问题闭环：#催办 群里问谁在跟 → #已解 答案归档直喂主题库。"""
import logging

log = logging.getLogger("wxbot.followup")


def _find_group(bot, grp_name):
    """qa_pending 存的是群 display 名，映射回配置里的群 id。"""
    for g in bot.cfg.data.get("groups") or []:
        if g.get("enabled") and (g.get("display") == grp_name or g.get("id") == grp_name):
            return g
    return None


def cmd_urge(bot, args):
    if not args:
        pend = bot.store.qa_pending(10)
        if not pend:
            return "没有待跟进的问题（#待审 可看全量）"
        return "待跟进：\n" + "\n".join(
            f"#{r['id']} [{r['grp']}] {r['questioner']}: {r['question'][:40]}"
            for r in pend) + "\n催办用法：#催办 <编号>"
    try:
        rid = int(args[0])
    except ValueError:
        return "编号得是数字"
    row = bot.store.get_qa_pending(rid)
    if not row or row.get("status") != "pending":
        return f"没找到待跟进问题 #{rid}"
    g = _find_group(bot, row.get("grp") or "")
    if not g:
        return f"「{row['grp']}」不在群配置里，没法替你到群里催"
    display = g.get("display") or g["id"]
    text = (f"之前 {row['questioner']} 问的「{(row['question'] or '')[:40]}」"
            "有小伙伴在跟进吗？有了 @我说一声～")
    ok, why = bot.gate.allow("followup", display, text)
    if not ok:
        return f"闸门拦住了（{why}），稍后再试"
    bot.send(display, text, "followup")
    return f"已到【{display}】催办：{(row['question'] or '')[:40]}"


def cmd_resolve(bot, args):
    if not args:
        return "用法：#已解 <编号> <答案一句话>（编号看 #待审）"
    try:
        rid = int(args[0])
    except ValueError:
        return "编号得是数字"
    answer = " ".join(args[1:]).strip()
    if not answer:
        return "把答案带上：#已解 <编号> <答案>"
    row = bot.store.get_qa_pending(rid)
    if not row or row.get("status") != "pending":
        return f"没找到待跟进问题 #{rid}"
    q = (row.get("question") or "").strip()
    bot.store.mark_qa_done(rid)
    g = _find_group(bot, row.get("grp") or "")
    if not g or not q:
        bot.store.log("followup", row.get("grp") or "?", row.get("questioner"),
                      f"已解但群不在配置，未入库：{q[:60]}")
        return f"已销项，但「{row['grp']}」不在群配置里，答案没进主题库"
    gid = g["id"]
    bot.store.add_mined(gid, [(q, answer)])
    from . import themes
    themes.assign_async(bot, gid)
    log.info("未解问题归档 #%d → 主题库 %s", rid, gid)
    return f"已解并归档主题库：【{g.get('display') or gid}】{q[:40]}"
