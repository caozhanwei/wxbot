# -*- coding: utf-8 -*-
"""历史消息全量冷启动导入流水线：避免画像、待解决诉求与问答库冷启动。

三路并行提取：
1. 活跃群友性格与诉求画像批量建立 (member_profiles)
2. 历史高频与共识问答沉淀 (mined)
3. 历史未解决诉求与待办问题发现 (pending_issues)
"""
import logging
import threading
import time
from collections import defaultdict

from . import group_logic

log = logging.getLogger("wxbot.bootstrap")

_status_lock = threading.Lock()
_STATUS = {
    "running": False,
    "gid": "",
    "gname": "",
    "total_msgs": 0,
    "total_profiles": 0,
    "done_profiles": 0,
    "done_qa": 0,
    "done_pending": 0,
    "error": "",
    "last_run": 0.0,
}


def status() -> dict:
    with _status_lock:
        return dict(_STATUS)


def _set_status(**kw):
    with _status_lock:
        _STATUS.update(kw)


def start_bootstrap_async(bot, gid: str, gname: str, limit: int = 1000) -> bool:
    with _status_lock:
        if _STATUS["running"]:
            return False
        _STATUS["running"] = True
        _STATUS["gid"] = gid
        _STATUS["gname"] = gname
        _STATUS["error"] = ""
        _STATUS["done_profiles"] = 0
        _STATUS["done_qa"] = 0
        _STATUS["done_pending"] = 0

    th = threading.Thread(
        target=run_bootstrap_sync,
        args=(bot, gid, gname, limit),
        daemon=True,
        name=f"Bootstrap-{gid[:8]}",
    )
    th.start()
    return True


def run_bootstrap_sync(bot, gid: str, gname: str, limit: int = 1000) -> bool:
    """全量扫描群历史消息并执行三路流水线。"""
    log.info("开始群 [%s](%s) 全量历史扫描 (limit=%d)...", gname, gid, limit)
    _set_status(running=True, gid=gid, gname=gname, done_profiles=0, done_qa=0, done_pending=0, error="")
    try:
        msgs = bot.driver.db.get_messages(gid, limit=limit)
        if not msgs:
            log.warning("未读取到群历史消息: %s", gid)
            _set_status(running=False, error="未读取到历史消息")
            return False

        # 正序排列以按时间线回放
        ordered = list(reversed(msgs))
        _set_status(total_msgs=len(ordered))

        # 1. 活跃成员画像批量扫描
        _pipeline_profiles(bot, gid, ordered)

        # 2. 问答库知识挖掘 (40条窗口滑动)
        _pipeline_qa_mining(bot, gid, ordered)

        # 3. 历史未解决诉求识别
        _pipeline_pending_issues(bot, gid, ordered)

        _set_status(running=False, last_run=time.time())
        log.info("群 [%s] 全量冷启动导入完成！", gname)
        return True
    except Exception as e:
        log.exception("全量冷启动导入异常: %s", e)
        _set_status(running=False, error=str(e))
        return False


def _pipeline_profiles(bot, gid: str, msgs: list):
    """流水线1：提取活跃群友发言并生成画像。"""
    member_msgs = defaultdict(list)
    for m in msgs:
        who = m.get("sender_username") or ""
        body = str(m.get("content") or "")
        if gid.endswith("@chatroom"):
            sender, parsed = group_logic.parse_group_content(body)
            who = who or sender
            body = parsed
        if not who or who == getattr(bot.driver, "self_id", ""):
            continue
        body = body.strip()
        if body and not body.startswith("<?xml") and len(body) >= 2:
            member_msgs[who].append(body)

    # 发言量 >= 2 条的成员建立画像
    candidates = [who for who, list_ in member_msgs.items() if len(list_) >= 2]
    _set_status(total_profiles=len(candidates))

    done = 0
    llm = getattr(bot.personas, "llm", None)
    for who in candidates:
        nick = bot.driver.display_of(who) or who
        recent_lines = member_msgs[who][-15:]
        profile_text = ""
        if llm:
            sys_prompt = (
                "你是微信群成员画像分析器。根据该群成员在群里的近期发言，"
                "提炼其【核心画像标签】：1.关注重点/诉求 2.性格与说话风格 3.身份或住址/角色特点（如业主栋数、职业等）。"
                "要求紧凑客观，100~300字以内，输出格式 JSON：{\"profile\": \"...\"}。"
            )
            usr_prompt = f"成员：{nick}（wxid={who}）\n历史发言片段：\n" + "\n".join(f"- {line}" for line in recent_lines)
            try:
                res = llm.chat_json(sys_prompt, usr_prompt, temperature=0.2)
                profile_text = str(res.get("profile") or "").strip()
            except Exception as e:
                log.warning("提炼群友 %s 画像失败: %s", nick, e)

        if not profile_text:
            profile_text = f"活跃成员，近来参与了讨论：{'; '.join(recent_lines[:3])}"

        bot.store.upsert_profile(gid, who, nick, profile_text)
        done += 1
        _set_status(done_profiles=done)


def _pipeline_qa_mining(bot, gid: str, msgs: list):
    """流水线2：知识库问答滑动窗口挖掘。"""
    llm = getattr(bot.personas, "llm", None)
    if not llm:
        return

    sys_prompt = (
        "你是群聊问答与知识挖掘助手。根据群聊片段，抽取群友之间讨论并得出明确共识的 Q&A 问答对（用于知识库沉淀）。"
        "过滤掉无营养寒暄、打招呼或未得出结论的争论。"
        "输出 JSON 列表，格式：[{\"q\": \"问题\", \"a\": \"明确答案\"}]，没有则返回 []。"
    )

    window_size = 40
    step = 30
    total_mined = 0
    for i in range(0, len(msgs), step):
        chunk = msgs[i : i + window_size]
        lines = []
        for m in chunk:
            who = m.get("sender_username") or "群友"
            body = str(m.get("content") or "").strip()
            if gid.endswith("@chatroom"):
                sender, parsed = group_logic.parse_group_content(body)
                who = sender or who
                body = parsed
            who_name = bot.driver.display_of(who) if hasattr(bot.driver, "display_of") else who
            if body and not body.startswith("<?xml"):
                lines.append(f"{who_name}: {body[:80]}")

        if len(lines) < 4:
            continue

        usr_prompt = "群聊记录：\n" + "\n".join(lines)
        try:
            pairs = llm.chat_json(sys_prompt, usr_prompt, temperature=0.1)
            if isinstance(pairs, list) and pairs:
                valid = []
                for p in pairs:
                    q = str(p.get("q") or "").strip()
                    a = str(p.get("a") or "").strip()
                    if q and a and len(q) >= 3 and len(a) >= 2:
                        valid.append((q, a))
                if valid:
                    bot.store.add_mined(gid, valid)
                    total_mined += len(valid)
                    _set_status(done_qa=total_mined)
        except Exception as e:
            log.warning("问答挖掘片段异常: %s", e)


def _pipeline_pending_issues(bot, gid: str, msgs: list):
    """流水线3：历史未解诉求与遗留待办问题提取。"""
    llm = getattr(bot.personas, "llm", None)
    if not llm:
        return

    sys_prompt = (
        "你是群聊待办与诉求挖掘助手。请识别群聊中群友提出的真实诉求、故障报修、求助或投诉，但在后续对话中未解或待解决的问题。"
        "输出 JSON 列表，格式：[{\"asker\": \"提问者\", \"question\": \"诉求/问题\", \"context\": \"上下文背景\"}]，没有则返回 []。"
    )

    window_size = 50
    step = 40
    total_pending = 0
    for i in range(0, len(msgs), step):
        chunk = msgs[i : i + window_size]
        lines = []
        for m in chunk:
            who = m.get("sender_username") or "群友"
            body = str(m.get("content") or "").strip()
            if gid.endswith("@chatroom"):
                sender, parsed = group_logic.parse_group_content(body)
                who = sender or who
                body = parsed
            who_name = bot.driver.display_of(who) if hasattr(bot.driver, "display_of") else who
            if body and not body.startswith("<?xml"):
                lines.append(f"{who_name}: {body[:80]}")

        if len(lines) < 3:
            continue

        usr_prompt = "群聊片段：\n" + "\n".join(lines)
        try:
            issues = llm.chat_json(sys_prompt, usr_prompt, temperature=0.1)
            if isinstance(issues, list) and issues:
                for item in issues:
                    asker = str(item.get("asker") or "群友").strip()
                    q = str(item.get("question") or "").strip()
                    ctx = str(item.get("context") or "").strip()
                    if q:
                        bot.store.add_pending_issue(gid, asker, q, ctx)
                        total_pending += 1
                        _set_status(done_pending=total_pending)
        except Exception as e:
            log.warning("未解问题识别异常: %s", e)
