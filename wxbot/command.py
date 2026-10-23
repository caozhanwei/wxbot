# -*- coding: utf-8 -*-
"""R4 一句话操控：自然语言 → 白名单意图路由（关键词快路径+LLM）→ 动作执行。

设计要点：
- 意图白名单，全部映射到既有函数（暂停/恢复/急停主动/补报/状态/约定/建提醒/
  查记录/开关群与联系人），不引入新的副作用路径；
- 关键词快路径只认整句级短语，绝不前缀匹配，防止误吞普通提问；
- 不像指令的话（_looks_commandish 不过）直接返回 None，不浪费一次 LLM 路由，
  对话台普通问答零额外延迟。
"""
import logging
import time

log = logging.getLogger("wxbot.command")

_MAX_AHEAD_DAYS = 60          # 与 reminders 一致：只接受 60 天内的提醒

_KW = {
    "暂停": "pause", "停一下": "pause", "暂停回复": "pause", "别回了": "pause",
    "恢复": "resume", "恢复回复": "resume", "继续回复": "resume",
    "补报": "missed",
    "急停主动": "stop_active", "急停": "stop_active",
    "恢复主动": "resume_active",
    "状态": "status", "运行状态": "status",
    "约定": "reminders", "约定列表": "reminders", "提醒列表": "reminders",
    "待办约定": "reminders",
}

# 触发 LLM 路由的信号词（快路径没命中时粗筛，普通聊天直接跳过）
_SIGNALS = ("暂停", "恢复", "补报", "急停", "状态", "约定", "提醒我", "提醒列表",
            "记一下", "帮我记", "查查", "查一下", "帮谁问过", "谁问过", "谁说过",
            "开一下", "关掉", "启用", "停用", "别回", "闭嘴")


INTENTS_DOC = {
    "pause": "暂停机器人全部自动回复",
    "resume": "恢复机器人自动回复",
    "stop_active": "急停全部主动动作（日报/约定提醒/关怀推送全停）",
    "resume_active": "恢复主动动作",
    "missed": "看补报汇总（错过的消息/@我未答/私聊找我的事）",
    "status": "看机器人运行状态",
    "reminders": "看待办约定列表",
    "remind_add": "新建一条约定提醒；args: who=要提醒的人, what=事, "
                  "date=YYYY-MM-DD, time=HH:MM（没说默认 09:00）",
    "recall": "跨群/私聊历史记录检索问答；args: query=检索内容",
    "enable": "启用某个群或联系人（只开那一个，不是全局）；args: target=群名或联系人名",
    "disable": "停用某个群或联系人（嫌吵/只停这个群）；args: target=群名或联系人名",
    "none": "不属于以上任何意图（普通提问/闲聊/答疑）",
}


def _kw_route(text):
    t = text.strip().rstrip("吧呢啊哦呀~～!！?？。.，, ")
    return {"intent": _KW[t]} if t in _KW else None


def _looks_commandish(text):
    return any(s in text for s in _SIGNALS)


def route(bot, text):
    """返回白名单意图 dict；None=不是一句话操控。"""
    text = (text or "").strip()
    if not text or text.startswith("#"):
        return None
    r = _kw_route(text)
    if r:
        return r
    if not _looks_commandish(text):
        return None
    try:
        r = bot.personas.route_intent(text, INTENTS_DOC)
    except Exception as e:
        log.info("意图路由失败: %s", e)
        return None
    if not r or str(r.get("intent") or "none") == "none":
        return None
    return r if r.get("intent") in INTENTS_DOC else None


def _fmt_due(due):
    return time.strftime("%m-%d %H:%M", time.localtime(due))


def execute(bot, r):
    """按白名单执行意图，返回回复文本。"""
    from . import digest, recall, reminders
    it = r.get("intent")
    if it == "pause":
        bot.state["paused"] = True
        return "已暂停自动回复，说「恢复」解除"
    if it == "resume":
        bot.state["paused"] = False
        return "已恢复自动回复"
    if it == "stop_active":
        bot.gate.stop()
        return "已急停全部主动动作（连日报/提醒都停），说「恢复主动」解除"
    if it == "resume_active":
        bot.gate.resume()
        return "已恢复主动动作"
    if it == "missed":
        return digest.report_missed_now(bot)
    if it == "status":
        return bot._do_admin("状态", [])
    if it == "reminders":
        return reminders.cmd_list(bot)
    if it == "remind_add":
        who = str(r.get("who") or "").strip()[:30]
        what = str(r.get("what") or "").strip()[:80]
        d = str(r.get("date") or "").strip()
        t = str(r.get("time") or "09:00").strip()
        if not who or not what:
            return "要建提醒的话说完整点：谁、什么事、什么时候"
        try:
            due = time.mktime(time.strptime(f"{d} {t}", "%Y-%m-%d %H:%M"))
        except ValueError:
            return "时间没听明白，试试「周五上午十点提醒张三交材料」这样的说法"
        now = time.time()
        if due < now or due > now + _MAX_AHEAD_DAYS * 86400:
            return f"只记 {_MAX_AHEAD_DAYS} 天内的约定哦"
        bot.store.add_reminder("一句话操控", who, what, due)
        return f"已记下：{who} {what}（{_fmt_due(due)} 到点提醒）"
    if it == "recall":
        q = str(r.get("query") or "").strip()
        return recall.answer(bot, q) if q else None
    if it in ("enable", "disable"):
        target = str(r.get("target") or "").strip()
        if not target:
            return f"要{'启用' if it == 'enable' else '停用'}哪个群/联系人？带上名字"
        return bot._do_admin("开" if it == "enable" else "关", [target])
    return None


def handle(bot, text):
    """路由+执行；None=不是操控指令，调用方走原链路。"""
    r = route(bot, text)
    if not r:
        return None
    try:
        return execute(bot, r)
    except Exception as e:
        log.warning("一句话操控执行失败: %s", e)
        return None
