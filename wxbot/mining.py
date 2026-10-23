# -*- coding: utf-8 -*-
"""群历史挖掘：全量/增量读群聊 → LLM 抽取问答 → 本地库 + markdown 导出。

知识库网关没有上传 API，"入库"落成两步：先攒在本地 mined 表，
网页端一键导出 markdown，再交给知识库管理员导入。
"""
import json
import logging
import re
import threading
import time

from . import group_logic

log = logging.getLogger("wxbot.mining")

# 与 driver._SENSITIVE 同款：命中即整条跳过，不送 LLM
_SENSITIVE = re.compile(r"\d{15,}|卡号|开户|身份证|密码|验证码")

_PAGE = 500            # DB 分页大小（降序翻页）
_WINDOW = 40           # 每批喂给 LLM 的消息条数
_OVERLAP = 8           # 相邻批重叠，减少问答跨边界丢抽
_LLM_INTERVAL = 0.3    # 相邻 LLM 调用间隔
AUTO_MIN_AGE = 600     # 自动模式只挖 10 分钟前的消息（等回答落地）
AUTO_INTERVAL = 600    # 同一群两次自动增量的最小间隔

_STATUS = {"running": False, "group": "", "done": 0, "total": 0,
           "pairs": 0, "error": ""}
_LOCK = threading.Lock()

_SYSTEM = (
    "你从微信群聊天记录中抽取可长期沉淀的知识。只输出 JSON 数组，"
    "每个元素形如 {\"q\": \"问题\", \"a\": \"回答\"}。要求："
    "q 是明确的求助/技术/业务/事实类问题；a 是群里给出的明确结论"
    "（可合并多人的有效要点）；闲聊、玩笑、没有结论的讨论一律不要；"
    "a 改写成客观陈述，去掉称呼和口语词，不超过 200 字；"
    "没有可抽取内容就只输出 []。"
)


def status():
    return dict(_STATUS)


def busy():
    return _STATUS["running"]


def start_mine(bot, gid, gname, full, auto=False):
    """后台启动一次挖掘；已有任务在跑返回 False。"""
    if not getattr(bot, "driver", None) or not getattr(bot, "personas", None):
        log.info("start_mine 拒绝：driver/personas 未就绪 (%s)", gname)
        _STATUS["error"] = "微信或 LLM 未就绪，无法挖掘"
        return False
    with _LOCK:
        if _STATUS["running"]:
            log.info("start_mine 拒绝：已有任务在跑 (%s)", gname)
            return False
        _STATUS.update(running=True, group=gname, done=0, total=0,
                       pairs=0, error="")

    def _job():
        log.info("挖掘线程开始 %s full=%s auto=%s", gname, full, auto)
        try:
            _do_mine(bot, gid, gname, full, auto)
        except Exception as e:
            log.exception("挖掘失败")
            _STATUS["error"] = f"{type(e).__name__}: {e}"
        finally:
            _STATUS["running"] = False
            log.info("挖掘线程结束 %s", gname)

    threading.Thread(target=_job, daemon=True).start()
    return True


def _do_mine(bot, gid, gname, full, auto):
    store, driver = bot.store, bot.driver
    since = 0 if full else (store.get_mined_seq(gid) or 0)
    msgs = _fetch_history(driver, gid, since)
    texts = _normalize(driver, msgs)
    if auto:
        cutoff = time.time() - AUTO_MIN_AGE
        texts = [t for t in texts if t["ts"] <= cutoff]
    _STATUS["total"] = len(texts)
    if not texts:
        log.info("挖掘 %s：没有新消息", gname)
        return
    seen = {(r["question"], r["answer"]) for r in store.mined_pairs(gid, limit=500)}
    fresh = []
    for win in _windows(texts):
        transcript = "\n".join(t["line"] for t in win)
        try:
            raw = bot.personas.llm.chat(_SYSTEM, transcript, temperature=0.2)
        except Exception as e:
            log.warning("挖掘批次失败（跳过该批）: %s", e)
            raw = ""
        for q, a in _parse_pairs(raw):
            if (q, a) not in seen:
                seen.add((q, a))
                fresh.append((q, a))
                _STATUS["pairs"] += 1
        _STATUS["done"] += len(win)
        time.sleep(_LLM_INTERVAL)
    if fresh:
        store.add_mined(gid, fresh)
    store.set_mined_seq(gid, texts[-1]["seq"])
    log.info("挖掘 %s：%d 条消息 → %d 对新问答", gname, len(texts), len(fresh))
    if fresh:
        from . import themes
        themes.assign_async(bot, gid)


def _fetch_history(driver, gid, since_seq):
    """按 sort_seq 升序返回 since_seq 之后（含全量）的群消息，分页翻旧。"""
    rows, offset = {}, 0
    while True:
        got = driver.db.get_messages(gid, limit=_PAGE, offset=offset)
        if not got:
            break
        oldest = None
        for m in got:
            seq = m["sort_seq"]
            oldest = seq if oldest is None else min(oldest, seq)
            if since_seq and seq <= since_seq:
                continue
            rows[seq] = m
        if len(got) < _PAGE:
            break
        if since_seq and oldest is not None and oldest <= since_seq:
            break
        offset += _PAGE
    return [rows[k] for k in sorted(rows)]


def _normalize(driver, msgs):
    """群消息 → {"seq","ts","line":"昵称: 正文"}，只留文本。"""
    names, out = {}, []
    for m in msgs:
        if m.get("type") != "文本":
            continue
        content = (m.get("content") or "").strip()
        if not content or content.startswith("<?xml"):
            continue
        if _SENSITIVE.search(content):
            continue
        sender, body = group_logic.parse_group_content(content)
        if sender and sender not in names:
            names[sender] = driver.display_of(sender)
        name = names.get(sender) or sender or "群友"
        out.append({"seq": m["sort_seq"], "ts": m.get("create_time") or 0,
                    "line": f"{name}: {body}"})
    return out


def _windows(items):
    step = max(1, _WINDOW - _OVERLAP)
    i = 0
    while i < len(items):
        yield items[i:i + _WINDOW]
        if i + _WINDOW >= len(items):
            break
        i += step


def _parse_pairs(text):
    """解析 LLM 输出的 JSON 数组，容忍 ```json 围栏与前后废话。"""
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        q = str(it.get("q", "")).strip()
        a = str(it.get("a", "")).strip()
        if q and a and len(q) <= 200 and len(a) <= 400:
            out.append((q, a))
    return out


def export_markdown(store, out_dir=None):
    """把本地累计的问答导出成 markdown，返回文件路径；无内容返回 None。"""
    import os
    from . import paths
    rows = store.mined_pairs(limit=1000000)
    if not rows:
        return None
    out_dir = out_dir or paths.resolve("exports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, time.strftime("问答沉淀_%Y%m%d_%H%M.md"))
    by_group = {}
    for r in rows:
        by_group.setdefault(r["grp"], []).append(r)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 群问答沉淀（自动挖掘）\n\n")
        f.write(f"导出时间：{time.strftime('%Y-%m-%d %H:%M')}，共 {len(rows)} 对\n")
        for grp in sorted(by_group):
            f.write(f"\n## 来自 {grp}\n\n")
            for it in by_group[grp]:
                f.write(f"**问：{it['question']}**\n\n答：{it['answer']}\n\n")
    return path
