# -*- coding: utf-8 -*-
"""群成员画像：按 群×成员 维护，答疑后增量更新；供回答注入与网页查看。

画像来源 = 旧画像 + 该成员最近发言（_SENSITIVE 已在 driver 层过滤），
生成 ≤120 字的身份线索/关注点/语气态度。同一成员 1 小时内不重复更新，
LLM 失败静默返回旧画像——画像只是增强项，绝不阻断回复。
"""
import logging
import threading
import time

from . import group_logic

log = logging.getLogger("wxbot.profiles")

THROTTLE_SEC = 3600.0   # 同一成员最短更新间隔
BATCH_TOP = 15          # 网页「刷新画像」一次最多更新的成员数

_STATUS = {"running": False, "group": "", "done": 0, "total": 0, "error": ""}
_LOCK = threading.Lock()

_SYSTEM = (
    "你是微信群助手，负责给群成员建画像。根据旧画像和他的最近发言，"
    "输出更新后的画像：身份线索（如住的楼栋/职业/在群里的角色）、"
    "近期关注的事、说话的语气态度。不超过120字，直接陈述、分号分隔要点，"
    "不要列表；涉及银行卡号/证件号/密码等敏感信息一律不记录。"
    '只输出 JSON：{"profile": "画像文本"}。'
)


def update_profile(personas, store, driver, grp, wxid, nickname, lines=None):
    """更新一个成员的画像，返回最新画像文本（失败/节流返回旧值或空串）。"""
    row = store.get_profile(grp, wxid)
    if row and time.time() - (row.get("updated") or 0) < THROTTLE_SEC:
        return row.get("profile") or ""
    old = (row or {}).get("profile") or ""
    try:
        if lines is None:
            lines = driver.member_lines(grp, wxid, limit=20)
        if not lines and not old:
            return ""
        user = f"群：{grp}\n成员：{nickname}"
        if old:
            user += f"\n旧画像：{old}"
        user += "\n他最近的发言：\n" + ("\n".join(lines) if lines else "（暂无新发言）")
        d = personas.llm.chat_json(_SYSTEM, user, temperature=0.2)
        p = str(d.get("profile") or "").strip()
        if p:
            store.upsert_profile(grp, wxid, nickname, p)
            log.info("画像更新 %s/%s: %s", grp, nickname, p[:50])
            return p
    except Exception as e:
        log.warning("画像更新失败(%s %s): %s", grp, nickname, e)
    return old


def active_members(driver, grp, top=BATCH_TOP):
    """群最近消息里最活跃的成员 wxid 列表（排除自己与系统）。"""
    msgs = driver.db.get_messages(grp, limit=200)
    seen = {}
    for m in msgs:
        who = m.get("sender_username") or ""
        body = str(m.get("content") or "")
        if grp.endswith("@chatroom") and not who:
            who = group_logic.parse_group_content(body)[0]
        if who and who != driver.self_id and who not in seen:
            seen[who] = True
        if len(seen) >= top:
            break
    return list(seen)


def refresh_group(bot, grp, top=BATCH_TOP, progress=None):
    """同步更新一群最活跃成员的画像，返回更新个数；progress(done) 可选回调。"""
    ids = active_members(bot.driver, grp, top)
    for i, wxid in enumerate(ids):
        update_profile(bot.personas, bot.store, bot.driver, grp, wxid,
                       bot.driver.display_of(wxid))
        if progress:
            progress(i + 1, len(ids))
    return len(ids)


def status():
    return dict(_STATUS)


def start_refresh(bot, gid, gname):
    """网页「刷新画像」入口：后台线程刷一群；已有任务在跑返回 False。"""
    if not getattr(bot, "driver", None) or not getattr(bot, "personas", None):
        _STATUS["error"] = "微信或 LLM 未就绪，无法刷新画像"
        return False
    with _LOCK:
        if _STATUS["running"]:
            return False
        _STATUS.update(running=True, group=gname, done=0, total=0, error="")

    def _job():
        try:
            refresh_group(bot, gid,
                          progress=lambda d, t: _STATUS.update(done=d, total=t))
            log.info("画像刷新完成 %s", gname)
        except Exception as e:
            log.exception("画像刷新失败")
            _STATUS["error"] = f"{type(e).__name__}: {e}"
        finally:
            _STATUS["running"] = False

    threading.Thread(target=_job, daemon=True).start()
    return True
