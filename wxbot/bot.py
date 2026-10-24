# -*- coding: utf-8 -*-
"""主编排：轮询所有已配置聊天 → 私聊人设回复 / 群答疑 / 群管理 / 管理员命令。"""
import logging
import queue
import random
import re
import threading
import time
from collections import deque

from answer import McpKBClient, KBError

from . import group_logic, paths, personas
from .kb import KBGateway
from .config import BotConfig
from .driver import WxDriver
from .gate import Gate
from .personas import PersonaEngine
from .store import Store

log = logging.getLogger("wxbot")

POLL_SEC_DEFAULT = 1.0   # 轮询间隔默认值（SQLCipher 增量读很轻，反应快优先）

_KB_SINK_MAX = 200       # 免@沉淀单次上传的最大消息条数（积压再多只保留最近这些）

_NO_AT_JUDGE = (
    "你是微信群助手的决策器，判断在没有被@的情况下是否值得主动接话。"
    '只输出 JSON：{"reply": true或false, "reason": "不超过20字的原因"}。'
    "reply=true：最近对话里有人明确提出问题、求助、求资源/文档，"
    "或正在讨论助手知识库能回答的技术/业务话题；"
    "reply=false：纯闲聊灌水、寒暄玩笑、表情刷屏、八卦、与知识无关，"
    "或最后一条是机器人自己说的话。拿不准一律 false——宁可少说不刷屏。"
)

_SINK_EXTRACT = (
    "你是知识库管理员，从群聊记录里提取“已形成明确答案”的问答知识对："
    "有人提出具体问题，且对话里给出了明确、可复用的答案；"
    "闲聊、寒暄、只有问题没有结论的一律不要。"
    '只输出 JSON：{"items": [{"q": "问题(30字内)", "a": "答案(120字内)"}]}，'
    "没有可提取的就输出空数组。"
)


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _limit_reply(group_cfg, text):
    """按群配置 max_reply_chars 截短群回复（0/未配=不限）。"""
    try:
        n = int(_num((group_cfg or {}).get("max_reply_chars"), 0))
    except (TypeError, ValueError):
        n = 0
    if n > 0 and text:
        return personas.limit_chars(text, n)
    return text


class Bot:
    def __init__(self, cfg_path="bot_config.yaml", with_web=True, cfg=None, state=None,
                 driver=None):
        self.cfg = cfg or BotConfig(cfg_path)
        self.store = Store(paths.resolve(self.cfg.db_file))
        self.driver = driver or WxDriver()
        self._setup_ocr()
        self._setup_asr()
        self.personas = PersonaEngine(self.cfg)
        self._kb = None
        self._kb_writer = None
        self._watch_queues = {}       # user -> Queue：每会话一个 worker，慢链路互不拖累
        self._wq_lock = threading.Lock()
        self.with_web = with_web
        # GUI 模式下与外壳共享同一对象，网页端看到的配置/暂停状态才是真实的
        self.state = state or {}
        self.state.setdefault("paused", False)
        self.state.setdefault("sent", 0)
        self.state.setdefault("start", time.time())
        self.state.setdefault("kb_fail_ts", 0)
        self.state.setdefault("fails", {})
        self.state.setdefault("no_at_last", {})
        self._last_sent_cache = {}    # display -> (text, ts) 防止同条消息短时间内连发刷屏
        self.watch = []
        self.gate = Gate(self)        # 主动动作闸门（急件/日报等统一过闸）

    def _setup_ocr(self):
        setup = getattr(self.driver, "setup_ocr", None)
        if callable(setup):
            setup(self.cfg.ocr_cfg)

    def _setup_asr(self):
        setup = getattr(self.driver, "setup_asr", None)
        if callable(setup):
            asr_cfg = self.cfg.data.get("asr") or {}
            setup(asr_cfg)

    # ---------- 基础 ----------
    @property
    def kb(self):
        if self._kb is None:
            self._kb = McpKBClient({"mcp": self.cfg.kb_cfg})
        return self._kb

    @property
    def kb_writer(self):
        """知识库写入端（免@消息沉淀用），与读端同源配置。"""
        if self._kb_writer is None:
            self._kb_writer = KBGateway({"mcp": self.cfg.kb_cfg})
        return self._kb_writer

    def build_watch(self):
        """按当前配置重建监控列表（热重载后调用，保留各会话水位）。"""
        old = {w["user"]: w["since"] for w in self.watch}
        self.watch = []
        for c in self.cfg.contacts:
            if not c.get("enabled"):
                continue
            uid = c.get("id") or self.driver.resolve_id(c.get("display", ""))
            if not uid:
                log.warning("联系人 %s 解析不到 id，跳过", c.get("display"))
                continue
            c = dict(c, id=uid)
            self.watch.append({"user": uid, "display": c.get("display") or uid,
                               "kind": "contact", "cfg": c, "since": old.get(uid)})
        for g in self.cfg.groups:
            if not g.get("enabled"):
                continue
            self.watch.append({"user": g["id"], "display": g.get("display") or g["id"],
                               "kind": "group", "cfg": g, "since": old.get(g["id"])})
        # 非白名单私聊被动监控（只记录进补报缓存，绝不回复）
        mp = self.cfg.app.get("missed_private")
        if mp is not False and not (isinstance(mp, dict) and mp.get("enabled") is False):
            known = {w["user"] for w in self.watch}
            try:
                recents = self.driver.list_recent_chats(limit=60)
            except Exception as e:
                log.warning("被动私聊发现失败: %s", e)
                recents = []
            for s in recents:
                uid = s.get("id") or ""
                if (s.get("is_group") or not uid or uid in known
                        or uid == self.driver.self_id or uid.startswith("gh_")
                        or (s.get("display") or "") == "文件传输助手"):
                    continue
                self.watch.append({"user": uid, "display": s.get("display") or uid,
                                   "kind": "contact_passive", "cfg": {},
                                   "since": old.get(uid)})
                known.add(uid)
        # 主人对话台：文件传输助手（主人自己的消息=对助手说话），默认开
        console = self.cfg.app.get("console")
        if not (isinstance(console, dict) and console.get("enabled") is False):
            uid = "filehelper"
            self.watch.append({"user": uid, "display": "文件传输助手",
                               "kind": "console", "cfg": {}, "since": old.get(uid)})
        for w in self.watch:
            if w["since"] is None:
                w["since"] = self.driver.baseline(w["user"])
        log.info("监控列表: %s", [(w["display"], w["kind"]) for w in self.watch])

    def send(self, display, text, kind="reply"):
        """统一发送出口：dry_run / 计数 / 落日志 / 防重复连发。"""
        text = str(text).strip()
        if not text:
            return False
        self.store.log(kind, display, "", text)
        if self.cfg.dry_run:
            log.info("[dry] → %s: %s", display, text[:60])
            self._last_sent_cache[display] = (text, time.time())
            return True

        # 真机发送去重检查：30秒内禁止对同一会话发送完全相同的消息
        last_t, last_time = self._last_sent_cache.get(display, ("", 0.0))
        if text == last_t and (time.time() - last_time < 30.0):
            log.warning("拦截重复连发 → %s: %s", display, text[:40])
            return True

        ok, why = self.driver.send_text(display, text)
        if ok:
            self.state["sent"] += 1
            self._last_sent_cache[display] = (text, time.time())
        else:
            log.warning("发送失败 → %s: %s", display, why)
        return ok

    def send_quote(self, display, text, target_name="", target_content="", kind="qa"):
        """带气泡引用发送出口：优先原生气泡引用，带去重与降级保护。"""
        text = str(text).strip()
        if not text:
            return False
        self.store.log(kind, display, target_name, text)
        if self.cfg.dry_run:
            log.info("[dry-quote] → %s (@%s): %s", display, target_name, text[:60])
            self._last_sent_cache[display] = (text, time.time())
            return True

        last_t, last_time = self._last_sent_cache.get(display, ("", 0.0))
        if text == last_t and (time.time() - last_time < 30.0):
            log.warning("拦截重复引用连发 → %s: %s", display, text[:40])
            return True

        ok, why = self.driver.send_quote(display, text, target_name=target_name, target_content=target_content)
        if ok:
            self.state["sent"] += 1
            self._last_sent_cache[display] = (text, time.time())
        else:
            log.warning("引用发送失败 → %s: %s", display, why)
        return ok

    def notify_admins(self, text):
        for a in self.cfg.admins:
            if a and a != self.driver.self_id:
                self.send(self.driver.display_of(a), text, "notify")

    # ---------- 主循环 ----------
    def run(self):
        self.build_watch()
        from . import profiles as _profiles
        _profiles.start_auto_refresh(self)
        if self.with_web:
            from . import webui
            webui.start_web(self)
        rc = self.cfg.reply_cfg
        max_replies = int(rc.get("max_replies", 0) or 0)
        max_runtime = int(rc.get("max_runtime_min", 0) or 0) * 60
        log.info("启动完成 dry_run=%s", self.cfg.dry_run)
        from . import care as _care
        from . import digest as _digest
        from . import reminders as _reminders
        from . import watchdog as _watchdog
        from .scheduler import DailyJob, Scheduler
        dd = self.cfg.app.get("daily_digest") or {}
        cc = self.cfg.app.get("care") or {}
        wk = self.cfg.app.get("weekly_report") or {}
        cr = self.cfg.app.get("complaint_review") or {}
        self._scheduler = Scheduler(self, [
            DailyJob("digest", int(dd.get("hour") or 21), fn=_digest.run_digest),
            DailyJob("care", int(cc.get("hour") or 10), fn=_care.run_care),
            DailyJob("weekly", int(wk.get("hour") or 9), minute=30,
                     fn=_digest.run_weekly),
            DailyJob("complaints", int(cr.get("hour") or 21), minute=45,
                     fn=_digest.run_complaint_review)])
        self._scheduler.start()
        while True:
            if self.cfg.maybe_reload():
                self.personas.refresh()
                self._kb = None
                self._kb_writer = None
                self._setup_ocr()
                self._setup_asr()
                self.build_watch()
                log.info("配置已热重载")
            if max_runtime and time.time() - self.state["start"] > max_runtime:
                log.info("达到运行时长上限，退出")
                return
            if max_replies and self.state["sent"] >= max_replies:
                log.info("达到发送条数上限，退出")
                return
            for w in list(self.watch):
                try:
                    msgs, w["since"] = self.driver.poll(w["user"], w["since"])
                except Exception as e:
                    log.warning("poll %s 失败: %s", w["display"], e)
                    self.state["poll_fails"] = (self.state.get("poll_fails") or 0) + 1
                    continue
                self.state["poll_fails"] = 0
                if not msgs:
                    continue
                self._dispatch(w, msgs)
            self._maybe_auto_mine()
            self._maybe_run_tasks()
            _reminders.maybe_extract(self)
            _reminders.maybe_fire(self)
            _watchdog.maybe_check(self)
            time.sleep(float(rc.get("poll_sec", POLL_SEC_DEFAULT)))

    def _dispatch(self, w, msgs):
        """消息入该会话专属队列，由常驻 worker 线程保序处理。

        主循环只管轮询拉新（≤poll_sec 拾取），任何一个会话的慢链路
        （LLM/KB 检索/UIA 发送）都不再拖住其他会话的应答。
        """
        with self._wq_lock:
            q = self._watch_queues.get(w["user"])
            if q is None:
                q = queue.Queue()
                self._watch_queues[w["user"]] = q
                threading.Thread(target=self._watch_worker, args=(q,), daemon=True,
                                 name="wxbot-w-" + (w["user"] or "")[-16:]).start()
        q.put((w, msgs))

    def _watch_worker(self, q):
        while True:
            w, msgs = q.get()
            try:
                if w["kind"] == "contact":
                    self._on_contact_msgs(w, msgs)
                elif w["kind"] == "contact_passive":
                    self._on_passive_msgs(w, msgs)
                elif w["kind"] == "console":
                    self._on_console_msgs(w, msgs)
                else:
                    self._on_group_msgs(w, msgs)
            except Exception as e:
                log.exception("处理 %s 消息异常: %s", w.get("display"), e)

    def _maybe_auto_mine(self):
        """配置了 mine 的群，定时增量挖掘历史入本地知识池。"""
        from . import mining
        for w in list(self.watch):
            if w["kind"] != "group" or not w["cfg"].get("mine"):
                continue
            marks = self.state.setdefault("mine_ts", {})
            if time.time() - marks.get(w["user"], 0) < mining.AUTO_INTERVAL:
                continue
            marks[w["user"]] = time.time()
            log.info("自动挖掘触发 %s", w["display"])
            mining.start_mine(self, w["user"], w["display"], full=False, auto=True)

    # ---------- 私聊 ----------
    def _on_contact_msgs(self, w, msgs):
        texts = []
        for m in msgs:
            if m["is_self"]:
                self.store.log("self_out", w["display"], "", m["content"][:80])
                continue
            if (m["sender"] in self.cfg.admins and m["content"].startswith("#")):
                self._admin_command(m["sender"], m["content"])
                continue
            if m["type"] == "文本" and not m["content"].startswith("<?xml"):
                texts.append(m)
            else:
                self.store.log("nontext", w["display"], m["sender_display"], m["type"])
        if texts and not self.state["paused"]:
            self._reply_contact(w, texts)

    def _on_passive_msgs(self, w, msgs):
        """被动私聊：只入补报缓存不回复；紧急词命中即时推送。"""
        from . import digest
        for m in msgs:
            try:
                digest.on_private_msg(self, w, m)
            except Exception as e:
                log.warning("被动私聊入库失败(%s): %s", w["display"], e)

    def _on_console_msgs(self, w, msgs):
        """主人对话台（文件传输助手）：is_self 文本=主人对助手说话。

        直接应答主人的提问，不过闸门、暂停时也可用；# 命令直通管理指令。
        """
        from . import recall
        for m in msgs:
            if not m.get("is_self") or m.get("type") != "文本":
                continue
            text = str(m.get("content") or "").strip()
            if not text or text.startswith("<"):
                continue
            # 自己刚发的（recall 回复/日报推送）不当成主人提问
            last_t, last_time = self._last_sent_cache.get(w["display"], ("", 0.0))
            if text == last_t and time.time() - last_time < 120:
                continue
            # 主人对机器人的吐槽也收进每日复盘（最有价值的改进线索）
            self._track_complaint(w, m, (), src="console")
            if text.startswith("#"):
                cmd = text.lstrip("#").split()
                self.send(w["display"],
                          self._do_admin(cmd[0] if cmd else "", cmd[1:]), "admin")
                continue
            from . import command
            out = command.handle(self, text)
            if out is not None:
                self.send(w["display"], out, "cmd")
                continue
            log.info("主人提问: %s", text[:60])
            try:
                ans = recall.answer(self, text)
            except Exception as e:
                log.warning("对话台问答失败: %s", e)
                ans = "这会儿没查明白，换个说法再问一次？"
            if ans:
                self.send(w["display"], ans, "recall")

    def _reply_contact(self, w, texts):
        from . import personas
        rc = self.cfg.reply_cfg
        # 连发合并等待：只对"刚发完、可能还在连发"的人生效；
        # 消息已静默超过窗口就立即处理，不再白等
        last_ts = max((float(m.get("ts") or 0) for m in texts), default=0.0)
        wait = float(rc.get("quiesce_sec", 5)) - (time.time() - last_ts)
        if wait > 0:
            time.sleep(wait)
        more, w["since"] = self.driver.poll(w["user"], w["since"])
        for m in more:
            if (not m["is_self"] and m["type"] == "文本"
                    and not m["content"].startswith("<?xml")):
                texts.append(m)
        for m in texts:
            self._track_complaint(w, m, src="private")
        last = texts[-1]["content"]
        log.info("私聊 %s: %s", w["display"], last[:60])
        try:
            ctx = self.driver.build_context(w["user"], int(rc.get("history_limit", 16)))
            memory = self.store.get_contact_memory(w["user"])
            reply = self.personas.chat_reply(w["cfg"].get("persona", "通用朋友"),
                                             w["display"], ctx, last, memory=memory)
        except Exception as e:
            log.warning("LLM 失败(%s): %s", w["display"], e)
            self._rewind_or_drop(w, texts)
            return
        if not reply:
            self._rewind_or_drop(w, texts)
            return

        # 多气泡切分与拟真打字延迟连发
        bubbles = personas.split_reply_bubbles(reply)
        all_ok = True
        for idx, bubble in enumerate(bubbles):
            delay = personas.human_delay_for_text(bubble)
            # 若不是 dry_run 则模拟真人打字等待
            if not self.cfg.dry_run:
                time.sleep(min(delay, 4.0 if idx > 0 else 6.0))
            if not self.send(w["display"], bubble, "reply"):
                all_ok = False
                break

        if all_ok:
            self.state["fails"][w["user"]] = 0
            # 异步提炼并沉淀好友生活事实记忆
            self._refresh_contact_memory_async(w["user"], w["display"], ctx, reply, memory)
        else:
            self._rewind_or_drop(w, texts)

    def _refresh_contact_memory_async(self, wxid, display, ctx, last_reply, current_mem):
        def _task():
            try:
                lines = ctx + [f"我: {last_reply}"]
                new_mem = self.personas.extract_contact_memory(display, lines, current_mem)
                if new_mem and new_mem != current_mem:
                    self.store.upsert_contact_memory(wxid, new_mem)
                    log.info("更新联系人 %s 长期记忆: %s", display, new_mem[:40])
            except Exception as e:
                log.warning("更新联系人记忆异常 %s: %s", display, e)
        threading.Thread(target=_task, daemon=True).start()

    def _rewind_or_drop(self, w, texts):
        """失败回退水位重试；连续 3 次放弃。"""
        n = self.state["fails"].get(w["user"], 0) + 1
        if n >= 3:
            w["since"] = texts[-1]["seq"]
            self.state["fails"][w["user"]] = 0
            self.notify_admins(f"[告警] 连续 {n} 次回复 {w['display']} 失败，已跳过该条")
        else:
            w["since"] = texts[0]["seq"] - 1
            self.state["fails"][w["user"]] = n

    # ---------- 群 ----------
    def _on_group_msgs(self, w, msgs):
        g = w["cfg"]
        qa = g.get("qa") or {}
        mg = g.get("manage") or {}
        names = self.driver.mention_names(g)

        def _is_at_target(m):
            if m["type"] != "文本" or m["content"].startswith("<?xml"):
                return False
            if m["is_self"] or not m["sender"] or self.state["paused"]:
                return False
            return group_logic.at_bot(m["content"], names) or \
                self.driver.self_id in (m.get("at_users") or [])

        # @答疑优先且一批只答最新的一条：积压时旧@早已被群聊顶替，逐条回旧问题
        # 正是"舍近求远答老问题"的根因；超过过气时限的@直接跳过不再补答。
        handled = set()
        seen = self.state.setdefault("qa_seen", {}).setdefault(
            w["user"], deque(maxlen=500))
        targets = [m for m in msgs
                   if qa.get("enabled") and _is_at_target(m) and m["seq"] not in seen]
        if targets:
            now = time.time()
            stale_sec = 60.0 * _num(qa.get("stale_min")
                                    or self.cfg.reply_cfg.get("qa_stale_min"), 10)
            pick = next((m for m in reversed(targets)
                         if now - float(m.get("ts") or now) <= stale_sec), None)
            if pick is None:
                log.info("群答疑 %s: %d 条@均已超过 %.0f 分钟，视为过气跳过",
                         w["display"], len(targets), stale_sec / 60.0)
            else:
                seen.append(pick["seq"])
                self._group_qa(w, pick)
            # 没被作答的@（被更新的@顶替/已过气）→ 补报入库
            from . import digest
            for m in targets:
                if m is not pick:
                    digest.record_at_missed(self, w, m)
            handled = {id(m) for m in targets}
        for m in msgs:
            if id(m) in handled:
                continue
            if m["type"] == "系统消息":
                if group_logic.leave_name(m["content"]):
                    self.notify_admins(f"[群秩序] {w['display']}：{(m['content'] or '')[:60]}")
                name = group_logic.join_name(m["content"])
                if name:
                    # 新成员登记（广告观察用，与欢迎开关无关）：[进群时间, 已观察条数, 已告警]
                    self.state.setdefault("new_members", {}).setdefault(
                        w["user"], {})[name] = [time.time(), 0, 0]
                    if mg.get("welcome"):
                        self._welcome(w, name)
                continue
            if m["is_self"] or not m["sender"]:
                continue
            if m["type"] != "文本" or m["content"].startswith("<?xml"):
                continue
            if self.state["paused"]:
                # 暂停期间 @我 的消息也记补报，不错过
                if group_logic.at_bot(m["content"], names) or \
                        self.driver.self_id in (m.get("at_users") or []):
                    from . import digest
                    digest.record_at_missed(self, w, m)
                continue
            self._track_flood(w, m)
            self._track_ads(w, m)
            self._track_complaint(w, m, names)
            word = None
            if mg.get("forbidden_words"):
                word = group_logic.forbidden_hit(m["content"], mg["forbidden_words"])
                if word:
                    self._warn(w, m, word)
                    continue
            if g.get("no_at_reply") or g.get("active"):
                try:
                    self._no_at_reply(w, m)
                except Exception as e:
                    log.exception("免@回复异常(%s): %s", w["display"], e)

    def _track_flood(self, w, m):
        """发言频次追踪：60 秒内连发超限 → 告警管理员（每成员每小时最多一次）。"""
        mx = int(_num(((w["cfg"].get("manage") or {}).get("order") or {}).get("flood_max"), 10))
        if mx <= 0:
            return
        key = (w["user"], m["sender"])
        dq = self.state.setdefault("flood", {}).setdefault(key, deque())
        now = time.time()
        dq.append(now)
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) < mx:
            return
        if now - self.state.setdefault("flood_alert", {}).get(key, 0) < 3600:
            return
        self.state["flood_alert"][key] = now
        who = m.get("sender_display") or m["sender"]
        self.notify_admins(f"[群秩序] {w['display']} 的 {who} 1 分钟内连发 "
                           f"{len(dq)} 条消息，疑似刷屏，请关注")

    def _track_ads(self, w, m):
        """新成员广告嫌疑：进群 24h 内的前 10 条消息，命中 ≥2 个广告特征
        → 告警管理员附证据（每人每群只告一次）。只告警不动人，踢人走 #踢 人工确认。"""
        order = ((w["cfg"].get("manage") or {}).get("order") or {})
        if not order.get("ads_watch", True):
            return
        gid = w["user"]
        news = (self.state.get("new_members") or {}).get(gid)
        if not news:
            return
        who = m.get("sender_display") or ""
        now = time.time()
        target = None
        for name, rec in list(news.items()):
            if now - rec[0] > 86400:
                del news[name]              # 观察窗过期，懒清理
                continue
            same = who and (who == name or (len(name) >= 2 and name in who)
                            or (len(who) >= 2 and who in name))
            if same and rec[1] < 10 and not rec[2]:
                target = (name, rec)
                break
        if target is None:
            return
        name, rec = target
        rec[1] += 1
        marks = group_logic.ads_markers(m["content"], order.get("ads_keywords") or [])
        if len(marks) < 2:
            return
        rec[2] = 1
        self.notify_admins(
            f"[广告嫌疑] {w['display']} 新成员「{who}」：{(m['content'] or '')[:60]}\n"
            f"特征：{'、'.join(marks[:5])}。确认违规可发：#踢 {who}")

    def _track_complaint(self, w, m, names=(), src="group"):
        """收集对机器人的吐槽（每日 LLM 复盘给优化建议）；只入库不即时打扰。"""
        try:
            quoted_bot = bool(m.get("is_quote") and m.get("quoted_user")
                              and m.get("quoted_user") in (list(names)
                              + [getattr(self.driver, "self_name", "") or ""]))
            hits = group_logic.complaint_hits(
                m.get("content") or "", bot_names=names,
                quoted_bot=quoted_bot, private=(src != "group"))
            if hits:
                self.store.add_complaint(
                    src, w["display"], m.get("sender") or "",
                    m.get("sender_display") or "",
                    m.get("content") or "", marks="、".join(hits[:4]),
                    ts=m.get("ts"))
        except Exception as e:
            log.debug("吐槽收集失败(%s): %s", w["display"], e)

    def _welcome(self, w, name):
        text = ((w["cfg"].get("manage") or {}).get("welcome_text")
                or "欢迎 {name} 入群！有问题可以直接 @我 提问～")
        text = group_logic.render_welcome(text, name)
        try:
            top = self.store.themes_list(w["user"])[:3]
        except Exception:
            top = []
        if top:
            text += "\n群里有沉淀的常见话题：" + \
                "、".join(t["theme"] for t in top) + "，@我问就行"
        self.send(w["display"], text, "welcome")

    def _newer_at_same_user(self, w, m):
        """只读窥探（不动水位）：本提问之后，同一发送者是否又@了新问题。

        答题链路要数秒，期间提问人追发新@是常态——旧问题多半已被自己
        顶替，答了就是"答的不是最后一次问的"。返回最新的那条@消息或 None。
        """
        try:
            msgs, _ = self.driver.poll(w["user"], m.get("seq") or 0)
        except Exception:
            return None
        if not msgs:
            return None
        names = self.driver.mention_names(w["cfg"])
        best = None
        for x in msgs:
            if x.get("is_self") or x.get("type") != "文本":
                continue
            c = x.get("content") or ""
            if not c or c.startswith("<?xml"):
                continue
            if not (group_logic.at_bot(c, names)
                    or self.driver.self_id in (x.get("at_users") or [])):
                continue
            if x.get("sender") == m.get("sender"):
                best = x
        return best

    def _supersede_if_newer(self, w, m):
        """同用户更新@已到：旧问题记补报，改答最新（递归一次，新问题窥探无更新即正答）。"""
        newer = self._newer_at_same_user(w, m)
        if newer is None:
            return False
        from . import digest
        log.info("群答疑 %s: %s 追发了新@，改答最新（旧问题转补报）",
                 w["display"], m.get("sender_display") or m.get("sender"))
        digest.record_at_missed(self, w, m)
        self.state.setdefault("qa_seen", {}).setdefault(
            w["user"], deque(maxlen=500)).append(newer["seq"])
        self._group_qa(w, newer)
        return True

    def _group_qa(self, w, m):
        g = w["cfg"]
        qa = g.get("qa") or {}
        names = self.driver.mention_names(g)
        maxc = int(_num(g.get("max_reply_chars"), 0))
        raw_msg_content = m.get("content") or ""
        question = group_logic.strip_at(raw_msg_content, names)
        if question == raw_msg_content.strip() and \
                self.driver.self_id in (m.get("at_users") or []):
            # 按称呼没剥掉 @（群名片≠微信昵称），又确认本机被 @：剥首个 @ 前缀
            question = group_logic.strip_generic_at(question)
        asker = m["sender_display"] or m["sender"]
        if not question:
            self.send_quote(w["display"], "在呢在呢，有事直接说哈 [呲牙]",
                            target_name=asker, target_content=raw_msg_content)
            return
        # 该用户追发了新@？答最新那次问的（这里早退最省；答题途中还会再查一次）
        if self._supersede_if_newer(w, m):
            return
        try:
            if self._maybe_task_command(w, m, question):
                return
        except Exception as e:
            log.warning("任务指令处理异常(%s): %s", w["display"], e)
        try:
            if self._maybe_command(w, m, question):
                return
        except Exception as e:
            log.warning("一句话操控处理异常(%s): %s", w["display"], e)
        if self._deflect_nonadmin_task(w, m, question):
            return
        rc = self.cfg.reply_cfg
        try:
            profile = (self.store.get_profile(w["user"], m["sender"]) or {}).get("profile") or ""
        except Exception:
            profile = ""
        try:
            self_profile = (self.store.get_profile(
                w["user"], getattr(self.driver, "self_id", "")) or {}).get("profile") or ""
        except Exception:
            self_profile = ""
        # 本地主题库先撞：该群沉淀过的问答直接转述作答，不查 KB（qa.local_first 可关）
        if qa.get("local_first", True):
            mined = self._local_qa_hit(w, question)
            if mined:
                mq, ma = mined
                log.info("主题库命中 %s: %s", w["display"], mq[:40])
                try:
                    reply = self.personas.group_answer(
                        g.get("persona", "通用朋友"), w["display"], [], question, asker,
                        profile=profile, self_profile=self_profile,
                        kb_ref=f"群里沉淀过的问题：{mq}\n当时的结论：{ma}",
                        avoid_lines=self._recent_replies(w["display"]), max_chars=maxc)
                except Exception as e:
                    log.warning("主题库转述失败(%s)，直出沉淀答案: %s", w["display"], e)
                    reply = ""
                reply = _limit_reply(w["cfg"], reply or f"群里之前聊过：{ma}")
                self.send_quote(w["display"], reply + "（群内沉淀）",
                                target_name=asker, target_content=raw_msg_content)
                return
        standalone, should = question, True
        # 提速①：KB 预取与提问理解并行（问题多数已独立，二者通常等价）
        kb_box = queue.Queue()

        def _prefetch():
            try:
                kb_box.put(self.kb.ask(question, w["display"]))
            except Exception as e:
                kb_box.put(e)
        threading.Thread(target=_prefetch, daemon=True).start()
        try:
            ctx = self.driver.build_context(w["user"], int(rc.get("history_limit", 16)))
            u = self.personas.understand_question(w["display"], ctx, question, asker, profile)
            standalone, should = u["standalone"], u["should_answer"]
        except Exception as e:
            log.warning("提问理解失败(%s)，用原始问题: %s", w["display"], e)
            ctx = []
        log.info("群答疑 %s: %s（独立问题: %s）", w["display"], question[:60], standalone[:60])
        self._refresh_profile_async(w, m["sender"], asker)
        # 理解/检索这段时间里提问人追发了新@：丢弃本轮，改答最新
        if self._supersede_if_newer(w, m):
            return
        if not should:
            # 闲聊/玩笑/打趣@机器人：接地气幽默接话，坚决拒绝死板客服模板
            casual = self._diversified(
                w["display"],
                lambda avoid: self.personas.casual_group_reply(
                    g.get("persona", "通用朋友"), w["display"], ctx, asker, question,
                    profile=profile, self_profile=self_profile, avoid_lines=avoid,
                    max_chars=maxc),
                "闲聊接话")
            if casual:
                self.send_quote(w["display"], _limit_reply(w["cfg"], casual),
                                target_name=asker, target_content=raw_msg_content)
            return
        try:
            if standalone == question:
                pre = kb_box.get(timeout=180)   # 预取已在路上，等它回来即可
                if isinstance(pre, Exception):
                    raise KBError(str(pre))
                answer, hit, grounded = pre
            else:
                # 改写成了不同的独立问题：预取作废，按独立问题重查
                answer, hit, grounded = self.kb.ask(standalone, w["display"])
        except KBError as e:
            log.warning("KB 调用失败: %s", e)
            if time.time() - self.state["kb_fail_ts"] > 600:
                self.state["kb_fail_ts"] = time.time()
                self.notify_admins(f"[告警] 知识库调用失败: {e}")
            if qa.get("llm_fallback"):
                try:
                    if not ctx:
                        ctx = self.driver.build_context(w["user"], int(rc.get("history_limit", 16)))
                    reply = self.personas.group_answer(g.get("persona", "通用朋友"),
                                                       w["display"], ctx, standalone,
                                                       asker, profile=profile,
                                                       self_profile=self_profile,
                                                       avoid_lines=self._recent_replies(w["display"]),
                                                       max_chars=maxc)
                    self.send_quote(w["display"], _limit_reply(w["cfg"], reply),
                                    target_name=asker, target_content=raw_msg_content)
                except Exception as ee:
                    log.warning("LLM 兜底也失败: %s", ee)
            return
        if hit:
            self.send_quote(w["display"], _limit_reply(w["cfg"], answer),
                            target_name=asker, target_content=raw_msg_content)
        elif grounded and qa.get("grounded_reply", True):
            self.store.add_qa_pending(w["display"], asker, standalone, answer)
            # 消除原始技术文档直接外露：通过人设引擎将参考资料润色为自然对话
            reply = self.personas.group_answer(g.get("persona", "通用朋友"),
                                               w["display"], ctx, standalone,
                                               asker, profile=profile, kb_ref=answer,
                                               self_profile=self_profile,
                                               avoid_lines=self._recent_replies(w["display"]),
                                               max_chars=maxc)
            self.send_quote(w["display"], _limit_reply(w["cfg"], reply),
                            target_name=asker, target_content=raw_msg_content)
        else:
            self.store.add_qa_pending(w["display"], asker, standalone)
            reply = self.personas.group_answer(g.get("persona", "通用朋友"),
                                               w["display"], ctx, standalone,
                                               asker, profile=profile,
                                               self_profile=self_profile,
                                               avoid_lines=self._recent_replies(w["display"]),
                                               max_chars=maxc)
            if not reply:
                reply = "这个我手头暂时没查到准确资料，已帮你记下反馈给群里小伙伴啦～"
            self.send_quote(w["display"], _limit_reply(w["cfg"], reply),
                            target_name=asker, target_content=raw_msg_content)
            self.notify_admins(f"[待沉淀] {w['display']} {asker}: {standalone[:80]}")

    def _local_qa_hit(self, w, question):
        """群精华主题库本地命中：与该群已沉淀问答共享字符二元组达阈值。"""
        from . import themes
        try:
            pairs = self.store.mined_pairs(w["user"])
        except Exception as e:
            log.warning("读主题库失败(%s): %s", w["display"], e)
            return None
        return themes.local_hit(pairs, question)

    def _recent_replies(self, display, limit=6):
        """本会话近期机器人已发出的文本（最新在前），供多样性判定与 prompt 注入。"""
        out = []
        try:
            for r in self.store.recent_logs(120):
                if r.get("kind") in ("reply", "no_at", "qa") and r.get("chat") == display:
                    t = str(r.get("detail") or "").strip()
                    if t:
                        out.append(t)
                        if len(out) >= limit:
                            break
        except Exception as e:
            log.warning("读取近期回复失败(%s): %s", display, e)
        return out

    def _diversified(self, display, gen, label):
        """gen(avoid_lines) → 文本；与近期已发换皮重复时带更强清单重试一次，仍重复则沉默。"""
        recent = self._recent_replies(display)
        text = gen(recent)
        if not text:
            return None
        if not personas.too_similar(text, recent):
            return text
        retry = gen(recent + [text])
        if retry and not personas.too_similar(retry, recent + [text]):
            return retry
        log.info("%s重复拦截 → %s: %s", label, display, (text or "")[:40])
        return None

    # ---------- 群定时任务（群主/管理员 @机器人 固化指令） ----------
    _TASK_KEYWORDS = ("总结", "提醒", "定时", "每天", "每日", "每周", "每月", "统计",
                      "汇总", "任务", "几点", "取消", "固定")
    # 非管理员的"定时+任务动词"式请求（如"每天晚上9点回顾总结群里的事"）：
    # 不是知识问题，硬走答疑只会答非所问——礼貌指路群主/管理员（真实翻车现场）
    _TASK_SCHED_RE = re.compile(r"每天|每晚|每周|每日|每月|定时|固定")
    _TASK_VERB_RE = re.compile(r"总结|回顾|播报|日报|周报|提醒|报告|统计|汇总")

    def _is_task_admin(self, w, m):
        """谁能在本群对机器人发号施令：全局 admins ∪ 群配置 admins（填昵称自动匹配）∪ 群主。"""
        allow_ids = {str(a).strip() for a in (self.cfg.admins or []) if str(a).strip()}
        owner = self.driver.chat_owner(w["user"]) if hasattr(self.driver, "chat_owner") else ""
        if owner:
            allow_ids.add(owner)
        sender = m.get("sender") or ""
        display = (m.get("sender_display") or "").strip()
        if sender in allow_ids:
            return True
        for a in {str(x).strip() for x in (w["cfg"].get("admins") or []) if str(x).strip()}:
            if a == sender or a == display:
                return True
            if len(a) >= 2 and a in display:   # 群名片常带前后缀，按包含匹配
                return True
        return False

    def _maybe_command(self, w, m, question):
        """群管理员 @机器人 的一句话操控（暂停/补报/建提醒/查记录/开关群）。

        命中并处理返回 True（调用方不再走答疑）；非管理员/不是指令返回 False。
        """
        from . import command
        if not self._is_task_admin(w, m):
            return False
        out = command.handle(self, question)
        if out is None:
            return False
        self.send_quote(w["display"], out,
                        target_name=m.get("sender_display") or m.get("sender") or "",
                        target_content=m.get("content") or "", kind="cmd")
        return True

    def _maybe_task_command(self, w, m, question=None):
        """群主/管理员的 @消息若是定时任务指令则处理并返回 True，否则返回 False 走原答疑。"""
        if question is None:
            question = group_logic.strip_at(m.get("content") or "",
                                            self.driver.mention_names(w["cfg"]))
        if not question:
            return False
        if not self._is_task_admin(w, m):
            return False
        if not any(k in question for k in self._TASK_KEYWORDS):
            return False
        try:
            parsed = self.personas.parse_task_command(question)
        except Exception as e:
            log.warning("任务指令解析失败(%s): %s", w["display"], e)
            return False
        if not parsed.get("is_task"):
            return False
        self._handle_task_cmd(w, m, parsed)
        return True

    def _deflect_nonadmin_task(self, w, m, question):
        """非管理员想安排定时任务（每晚总结/定时提醒）：不硬答知识库，指路管理员。

        群里真实翻过车：群友说"每天晚上9点回顾总结群里发生的事情"，
        落到答疑链路后知识库检索出一堆不相干内容，答非所问被吐槽。
        """
        if self._is_task_admin(w, m):
            return False
        if not (self._TASK_SCHED_RE.search(question)
                and self._TASK_VERB_RE.search(question)):
            return False
        asker = m.get("sender_display") or m.get("sender") or ""
        self.send_quote(
            w["display"],
            "这事儿我一个人定不了哈，得群主或管理员发话～"
            "让TA @我 说一声「每天晚上9点总结群消息」就能安排上 [抱拳]",
            target_name=asker, target_content=m.get("content") or "", kind="qa")
        return True

    def _handle_task_cmd(self, w, m, parsed):
        action = parsed.get("action")
        asker = m.get("sender_display") or m.get("sender") or "管理"
        raw = m.get("content") or ""
        if action == "list":
            tasks = self.store.list_tasks(w["user"])
            if not tasks:
                self.send_quote(w["display"], "现在这个群还没有定时任务哈",
                                target_name=asker, target_content=raw)
                return
            lines = [f"{i}. 每天 {int(t['hour']):02d}:{int(t['minute']):02d} "
                     f"{t['instruction']}（来自 {t.get('created_by_name') or '管理'}）"
                     for i, t in enumerate(tasks, 1)]
            self.send_quote(w["display"], "当前定时任务：\n" + "\n".join(lines),
                            target_name=asker, target_content=raw)
            return
        if action == "cancel":
            tasks = self.store.list_tasks(w["user"])
            idx = max(1, int(parsed.get("index") or 1))
            if not tasks or idx > len(tasks):
                self.send_quote(w["display"],
                                "没找到这个任务，先发「看看现在的定时任务」我列给你",
                                target_name=asker, target_content=raw)
                return
            t = tasks[idx - 1]
            self.store.remove_task(t["id"])
            self.send_quote(w["display"],
                            f"已取消：每天 {int(t['hour']):02d}:{int(t['minute']):02d} "
                            f"{t['instruction']}",
                            target_name=asker, target_content=raw)
            return
        # create
        hour = int(parsed.get("hour", 21))
        minute = int(parsed.get("minute", 0))
        desc = parsed.get("desc") or "定时任务"
        self.store.add_task(w["user"], w["display"], hour, minute, desc,
                            created_by=m.get("sender", ""),
                            created_by_name=asker)
        self.send_quote(
            w["display"],
            f"好嘞，已固化：每天 {hour:02d}:{minute:02d} 我会在群里发【{desc}】，"
            "到点叫大家 [呲牙]",
            target_name=asker, target_content=raw)

    def _maybe_run_tasks(self):
        """每 30 秒巡检一次到期的定时任务，后台线程执行。"""
        if time.time() - self.state.get("last_task_check", 0) < 30:
            return
        self.state["last_task_check"] = time.time()
        for t in self.store.due_tasks():
            w = next((x for x in self.watch if x["user"] == t.get("grp")), None)
            if not w:
                continue
            self.store.mark_run(t["id"])
            log.info("定时任务触发 %s %02d:%02d: %s", t.get("grp_name"),
                     int(t.get("hour") or 0), int(t.get("minute") or 0),
                     (t.get("instruction") or "")[:40])
            threading.Thread(target=self._run_task, args=(w, t), daemon=True).start()

    def _run_task(self, w, t):
        """执行一个定时任务（v1：近 24h 群消息按指令总结播报）。"""
        from . import mining
        try:
            rows = self.driver.db.get_messages(w["user"], limit=800) or []
            cutoff = time.time() - 24 * 3600
            rows = [r for r in rows
                    if (r.get("create_time") or 0) >= cutoff and r.get("sender_username")]
            lines = [x["line"] for x in mining._normalize(self.driver, rows)]
            lines = lines[-300:]
            if not lines:
                self.send(w["display"], "过去24小时群里挺安静，没什么要播报的～", "reply")
                return
            text = self.personas.summarize_day(
                w["display"], lines, t.get("instruction") or "总结今天群里的消息")
            if not text:
                raise RuntimeError("总结为空")
            self.send(w["display"], text, "reply")
        except Exception as e:
            log.warning("定时任务执行失败(%s): %s", t.get("grp_name"), e)
            self.notify_admins(f"[告警] 定时任务执行失败 {t.get('grp_name')}: {e}")

    def _refresh_profile_async(self, w, wxid, nickname):
        """后台线程更新提问者画像，LLM 慢也不挡主循环。"""
        from . import profiles
        threading.Thread(
            target=profiles.update_profile,
            args=(self.personas, self.store, self.driver,
                  w["user"], wxid, nickname),
            daemon=True).start()

    def _warn(self, w, m, word):
        mg = w["cfg"].get("manage") or {}
        threshold = int(mg.get("warn_threshold", 2) or 2)
        member = m["sender_display"] or m["sender"]
        nth = self.store.bump_warn(w["user"], member, word)
        log.info("违禁 %s %s 第%d次: %s", w["display"], member, nth, word)
        msg = self.personas.warn_message(mg.get("warn_persona", "群管家-热心"),
                                         member, word, nth, threshold)
        self.send(w["display"], msg, "warn")
        if nth >= threshold:
            self.notify_admins(f"[群管理] {w['display']} 的 {member} 已 {nth} 次触发违禁词"
                               f"（{word}），建议移出群（需在微信中人工操作）")
            self.store.audit("bot", "warn_escalated", f"{w['display']} {member} {word}")

    # ---------- 免@上下文回复 ----------
    def _no_at_reply(self, w, m):
        """群里没人@机器人时，按最近上下文判断要不要接话（仅 no_at_reply 打开的群）。

        链路：冷却检查 → 增量沉淀进知识库 → LLM 判定值不值得接话 →
        带 KB 检索参考生成回复。任何一步失败都只记日志，不影响主循环；
        dry_run 时照常判断，由 send() 只记日志不发送。
        """
        g = w["cfg"]
        rc = self.cfg.reply_cfg
        cooldown = 60.0 * _num(g.get("no_at_cooldown_min") or rc.get("no_at_cooldown_min"), 3)
        marks = self.state.setdefault("no_at_last", {})
        if time.time() - marks.get(w["user"], 0) < cooldown:
            return
        try:
            context = self.driver.build_context(
                w["user"], int(g.get("no_at_context") or rc.get("no_at_context") or 20))
        except Exception as e:
            log.warning("免@读取上下文失败(%s): %s", w["display"], e)
            return
        if not context:
            return
        self._sink_to_kb_async(w)
        active = bool(w["cfg"].get("active"))
        if not active and not self._judge_no_at(w, context):
            return
        reply = self._diversified(
            w["display"],
            lambda avoid: self._no_at_answer(w, context, active=active, avoid=avoid),
            "免@接话")
        if not reply:
            return
        text = _limit_reply(w["cfg"], reply)
        if w["cfg"].get("no_at_quote", True):
            # 接话引用触发那条消息：群里消息多时，纯文本回复没人知道在答谁
            sent = self.send_quote(
                w["display"], text,
                target_name=m.get("sender_display") or m.get("sender") or "",
                target_content=m.get("content") or "", kind="no_at")
        else:
            sent = self.send(w["display"], text, "no_at")
        if sent:
            marks[w["user"]] = time.time()

    def _sink_to_kb_async(self, w):
        """KB 沉淀丢后台线程：提炼+逐条入库常要十几秒，绝不占应答路径；
        同群进行中直接跳过，避免并发重入重复提炼同一段消息。"""
        marks = self.state.setdefault("sink_running", {})
        if marks.get(w["user"]):
            return
        marks[w["user"]] = True

        def _job():
            try:
                self._sink_to_kb(w)
            finally:
                marks[w["user"]] = False
        threading.Thread(target=_job, daemon=True,
                         name="wxbot-sink-" + (w["user"] or "")[-16:]).start()

    def _sink_to_kb(self, w):
        """把水位线后的新群消息（_SENSITIVE 过滤后）提炼成问答对写入知识库；失败静默降级待重试。"""
        from . import mining
        gid, gname = w["user"], w["display"]
        try:
            since = self.store.get_kb_seq(gid)
            if since:
                rows = mining._fetch_history(self.driver, gid, since)
            else:  # 首次沉淀：不回翻全量历史，只带最近一段
                rows = sorted(self.driver.db.get_messages(gid, limit=_KB_SINK_MAX),
                              key=lambda r: r["sort_seq"])
            lines = mining._normalize(self.driver, rows)
            if not lines:
                return
            if len(lines) > _KB_SINK_MAX:
                log.info("KB 沉淀积压 %d 条，只保留最近 %d 条", len(lines), _KB_SINK_MAX)
                lines = lines[-_KB_SINK_MAX:]
            d = self.personas.llm.chat_json(
                _SINK_EXTRACT, "来源群：" + gname + "\n"
                + "\n".join(t["line"] for t in lines), temperature=0.0)
            items = d.get("items") if isinstance(d, dict) else None
            pushed = skipped = failed = 0
            for it in items or []:
                q = str((it or {}).get("q") or "").strip()
                a = str((it or {}).get("a") or "").strip()
                if not q or not a:
                    skipped += 1
                    continue
                try:
                    self.kb_writer.add_qa(q, a, context="来源群：" + gname)
                    pushed += 1
                except Exception as e:
                    failed += 1
                    log.warning("KB 问答入库失败(%s): %s %s", gname, q[:30], e)
            if failed and not pushed:
                raise RuntimeError("%d 条问答全部入库失败" % failed)
            self.store.set_kb_seq(gid, lines[-1]["seq"])
            log.info("KB 沉淀 %s：%d 行 → 入库 %d 条问答%s",
                     gname, len(lines), pushed, "（本批无可沉淀）" if not pushed else "")
        except Exception as e:      # KBError 及其他：KB 挂了绝不阻断回复
            log.warning("KB 沉淀失败(%s)，跳过待下次重试: %s", gname, e)

    def _judge_no_at(self, w, context):
        """LLM 判定这段对话是否值得机器人接话；失败视为不值得（宁可沉默）。"""
        try:
            d = self.personas.llm.chat_json(
                _NO_AT_JUDGE,
                "群：" + w["display"] + "\n最近对话：\n"
                + "\n".join(context[-12:]) + "\n\n判断：",
                temperature=0.0)
            ok = bool(d.get("reply"))
            log.info("免@判定 %s → %s（%s）", w["display"],
                     "接话" if ok else "沉默", d.get("reason", ""))
            return ok
        except Exception as e:
            log.warning("免@判定失败(%s)，本次不接话: %s", w["display"], e)
            return False

    def _no_at_answer(self, w, context, active=False, avoid=None):
        """生成免@回复，prompt 里并入知识库检索参考；检索失败就无参考作答。"""
        ref = ""
        try:
            ref = self.kb.search("\n".join(context[-5:]), top_k=3)
        except Exception as e:
            log.warning("KB 检索失败(%s)，本次无参考作答: %s", w["display"], e)
        try:
            return self.personas.context_reply(
                w["cfg"].get("persona", "群管家-热心"), w["display"], context, ref,
                active=active, avoid_lines=avoid,
                max_chars=int(_num(w["cfg"].get("max_reply_chars"), 0)))
        except Exception as e:
            log.warning("免@回复生成失败(%s): %s", w["display"], e)
            return ""

    # ---------- 管理员命令 ----------
    def _admin_command(self, sender, content):
        cmd = content.strip().lstrip("#").split()
        name = cmd[0] if cmd else ""
        reply = self._do_admin(name, cmd[1:])
        self.send(self.driver.display_of(sender), reply, "admin")

    def _do_admin(self, name, args):
        if name in ("帮助", "help", ""):
            return ("命令：#状态 #暂停 #恢复 #补报 #吐槽 #急停主动 #恢复主动 "
                    "#开 <名> #关 <名> #重载 #待审 #提醒 #销项 #催办 #已解 "
                    "#踢 <昵称> [群名] #确认踢 #取消踢\n"
                    "（联系人/群名单与开关在 bot_config.yaml 或网页配置）")
        if name == "状态":
            ups = int(time.time() - self.state["start"])
            ws = ", ".join(f'{w["display"]}({"群" if w["kind"]=="group" else "私"})'
                           for w in self.watch) or "无"
            gate = "，主动动作已急停" if self.gate.stopped else ""
            return (f"运行 {ups//60} 分钟，已发送 {self.state['sent']} 条，"
                    f"{'已暂停' if self.state['paused'] else '正常'}{gate}\n监控: {ws}")
        if name == "补报":
            from . import digest
            return digest.report_missed_now(self)
        if name == "吐槽":
            from . import digest
            return digest.report_complaints_now(self)
        if name == "急停主动" or (name == "急停" and args[:1] == ["主动"]):
            self.gate.stop()
            return "已急停全部主动动作（急件/日报推送都停），#恢复主动 解除"
        if name == "恢复主动" or (name == "恢复" and args[:1] == ["主动"]):
            self.gate.resume()
            return "已恢复主动动作"
        if name == "暂停":
            self.state["paused"] = True
            return "已暂停自动回复（管理员命令仍可用，#恢复 恢复）"
        if name == "恢复":
            self.state["paused"] = False
            return "已恢复自动回复"
        if name in ("开", "关") and args:
            target = args[0]
            hit = False
            for kind in ("contacts", "groups"):
                for item in self.cfg.data.get(kind) or []:
                    if item.get("display") == target or item.get("id") == target:
                        item["enabled"] = (name == "开")
                        hit = True
                if hit:
                    break
            if hit:
                self.build_watch()
                return f"已{'启用' if name == '开' else '停用'} {target}"
            return f"没找到 {target}"
        if name == "重载":
            self.cfg.reload()
            self.personas.refresh()
            self._kb = None
            self._kb_writer = None
            self.build_watch()
            return "配置已重载"
        if name == "待审":
            rows = self.store.qa_pending(10)
            if not rows:
                return "暂无待沉淀问题"
            return "\n".join(f'#{r["id"]} [{r["grp"]}] {r["questioner"]}: {r["question"][:40]}'
                             for r in rows)
        if name == "提醒":
            from . import reminders
            return reminders.cmd_list(self)
        if name == "销项":
            from . import reminders
            return reminders.cmd_done(self, args)
        if name == "催办":
            from . import followup
            return followup.cmd_urge(self, args)
        if name == "已解":
            from . import followup
            return followup.cmd_resolve(self, args)
        if name == "踢" and args:
            return self._cmd_kick(args)
        if name == "确认踢":
            return self._cmd_kick_confirm()
        if name == "取消踢":
            if self.state.get("kick_pending"):
                self.state["kick_pending"] = None
                return "已取消移出操作"
            return "没有待确认的移出操作"
        return f"未知命令 #{name}，#帮助 查看"

    # ---------- #踢 二次确认（破坏性动作必须人工点头） ----------

    def _kick_evidence(self, gid, member):
        """近 80 条里找 member 最新一条发言（展示证据 + 定位探针）。"""
        try:
            rows = self.driver.db.get_messages(gid, limit=80) or []
        except Exception:
            return None
        for r in reversed(rows):
            nick = (r.get("sender_nickname") or "").strip()
            if nick and (nick == member or member in nick or nick in member):
                return r
        return None

    def _cmd_kick(self, args):
        groups = [w for w in self.watch if w.get("kind") == "group"]
        if not groups:
            return "没有监控中的群"
        member = " ".join(args).strip()
        gname = None
        hit = next((g for g in groups if g["display"] == args[-1]), None)
        if hit is not None and len(args) >= 2:
            gname, member = args[-1], " ".join(args[:-1]).strip()
        elif len(groups) == 1:
            gname = groups[0]["display"]
        else:
            return ("监控了多个群，请带上群名：#踢 昵称 群名\n"
                    + " / ".join(g["display"] for g in groups[:6]))
        if not member:
            return "用法：#踢 昵称 [群名]"
        if member == (getattr(self.driver, "self_name", "") or ""):
            return "这是机器人自己的昵称，不动"
        gid = next(g["user"] for g in groups if g["display"] == gname)
        probe = ""
        row = self._kick_evidence(gid, member)
        if row:
            probe = (row.get("content") or "").split("\n")[-1].strip()[:40]
        self.state["kick_pending"] = {"gid": gid, "gname": gname, "member": member,
                                      "probe": probe, "ts": time.time()}
        ev = f"TA 近期发言：{probe}" if probe else "（近 80 条没找到 TA 的发言，请核对昵称）"
        return (f"准备把【{gname}】的「{member}」移出群聊\n{ev}\n"
                f"确认发 #确认踢 · 取消发 #取消踢（5 分钟内有效）")

    def _cmd_kick_confirm(self):
        p = self.state.get("kick_pending")
        if not p:
            return "没有待确认的移出操作，先用 #踢 昵称"
        if time.time() - p.get("ts", 0) > 300:
            self.state["kick_pending"] = None
            return f"确认已超时（超 5 分钟），请重新发 #踢 {p.get('member', '')}"
        self.state["kick_pending"] = None        # 先清待确认，防重复执行
        gname, member, probe = p["gname"], p["member"], p.get("probe") or ""
        if self.cfg.dry_run:
            self.store.audit("admin", "kick_dry", f"[{gname}] {member}")
            return f"(演练) dry_run 模式不实际移出：【{gname}】「{member}」"

        def _go():
            ok = False
            try:
                ok = self.driver.kick_member(gname, member, probe=probe)
            except Exception as e:
                log.warning("移出群聊执行异常(%s/%s): %s", gname, member, e)
            if ok:
                self.store.audit("admin", "kick", f"[{gname}] {member}")
                self.notify_admins(f"已移出【{gname}】的「{member}」")
            else:
                self.notify_admins(
                    f"自动移出没走完（没定位到消息或菜单），请手动处理："
                    f"【{gname}】群设置 → 群成员 → 「{member}」→ 移出群聊")

        threading.Thread(target=_go, daemon=True).start()
        return f"正在移出【{gname}】的「{member}」，结果稍后通知"
