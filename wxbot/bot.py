# -*- coding: utf-8 -*-
"""主编排：轮询所有已配置聊天 → 私聊人设回复 / 群答疑 / 群管理 / 管理员命令。"""
import logging
import random
import threading
import time

from answer import McpKBClient, KBError

from . import group_logic, paths
from .kb import KBGateway
from .config import BotConfig
from .driver import WxDriver
from .personas import PersonaEngine
from .store import Store

log = logging.getLogger("wxbot")

_KB_SINK_MAX = 200       # 免@沉淀单次上传的最大消息条数（积压再多只保留最近这些）

_NO_AT_JUDGE = (
    "你是微信群助手的决策器，判断在没有被@的情况下是否值得主动接话。"
    '只输出 JSON：{"reply": true或false, "reason": "不超过20字的原因"}。'
    "reply=true：最近对话里有人明确提出问题、求助、求资源/文档，"
    "或正在讨论助手知识库能回答的技术/业务话题；"
    "reply=false：纯闲聊灌水、寒暄玩笑、表情刷屏、八卦、与知识无关，"
    "或最后一条是机器人自己说的话。拿不准一律 false——宁可少说不刷屏。"
)


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


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
        if self.cfg.dry_run:
            log.info("[dry-quote] → %s (@%s): %s", display, target_name, text[:60])
            self._last_sent_cache[display] = (text, time.time())
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
                    continue
                if not msgs:
                    continue
                if w["kind"] == "contact":
                    self._on_contact_msgs(w, msgs)
                else:
                    self._on_group_msgs(w, msgs)
            self._maybe_auto_mine()
            time.sleep(float(rc.get("poll_sec", 3)))

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

    def _reply_contact(self, w, texts):
        from . import personas
        rc = self.cfg.reply_cfg
        time.sleep(float(rc.get("quiesce_sec", 5)))          # 合并连发
        more, w["since"] = self.driver.poll(w["user"], w["since"])
        for m in more:
            if (not m["is_self"] and m["type"] == "文本"
                    and not m["content"].startswith("<?xml")):
                texts.append(m)
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
        for m in msgs:
            if m["type"] == "系统消息":
                if mg.get("welcome"):
                    name = group_logic.join_name(m["content"])
                    if name:
                        self._welcome(w, name)
                continue
            if m["is_self"] or not m["sender"]:
                continue
            if m["type"] != "文本" or m["content"].startswith("<?xml"):
                continue
            if self.state["paused"]:
                continue
            if qa.get("enabled") and group_logic.at_bot(m["content"], names):
                self._group_qa(w, m)      # @机器人：原有答疑链路，不受免@冷却限制
                continue
            word = None
            if mg.get("forbidden_words"):
                word = group_logic.forbidden_hit(m["content"], mg["forbidden_words"])
                if word:
                    self._warn(w, m, word)
                    continue
            if g.get("no_at_reply"):
                try:
                    self._no_at_reply(w, m)
                except Exception as e:
                    log.exception("免@回复异常(%s): %s", w["display"], e)

    def _welcome(self, w, name):
        text = ((w["cfg"].get("manage") or {}).get("welcome_text")
                or "欢迎 {name} 入群！有问题可以直接 @我 提问～")
        self.send(w["display"], text.format(name=name), "welcome")

    def _group_qa(self, w, m):
        g = w["cfg"]
        qa = g.get("qa") or {}
        names = self.driver.mention_names(g)
        raw_msg_content = m.get("content") or ""
        question = group_logic.strip_at(raw_msg_content, names)
        asker = m["sender_display"] or m["sender"]
        if not question:
            self.send_quote(w["display"], "在呢在呢，有事直接说哈 [呲牙]",
                            target_name=asker, target_content=raw_msg_content)
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
        standalone, should = question, True
        try:
            ctx = self.driver.build_context(w["user"], int(rc.get("history_limit", 16)))
            u = self.personas.understand_question(w["display"], ctx, question, asker, profile)
            standalone, should = u["standalone"], u["should_answer"]
        except Exception as e:
            log.warning("提问理解失败(%s)，用原始问题: %s", w["display"], e)
            ctx = []
        log.info("群答疑 %s: %s（独立问题: %s）", w["display"], question[:60], standalone[:60])
        self._refresh_profile_async(w, m["sender"], asker)
        if not should:
            # 闲聊/玩笑/打趣@机器人：接地气幽默接话，坚决拒绝死板客服模板
            casual = self.personas.casual_group_reply(
                g.get("persona", "通用朋友"), w["display"], ctx, asker, question,
                profile=profile, self_profile=self_profile)
            self.send_quote(w["display"], casual, target_name=asker, target_content=raw_msg_content)
            return
        try:
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
                                                       self_profile=self_profile)
                    self.send_quote(w["display"], reply, target_name=asker, target_content=raw_msg_content)
                except Exception as ee:
                    log.warning("LLM 兜底也失败: %s", ee)
            return
        if hit:
            self.send_quote(w["display"], answer, target_name=asker, target_content=raw_msg_content)
        elif grounded and qa.get("grounded_reply", True):
            self.store.add_qa_pending(w["display"], asker, standalone, answer)
            # 消除原始技术文档直接外露：通过人设引擎将参考资料润色为自然对话
            reply = self.personas.group_answer(g.get("persona", "通用朋友"),
                                               w["display"], ctx, standalone,
                                               asker, profile=profile, kb_ref=answer,
                                               self_profile=self_profile)
            self.send_quote(w["display"], reply, target_name=asker, target_content=raw_msg_content)
        else:
            self.store.add_qa_pending(w["display"], asker, standalone)
            reply = self.personas.group_answer(g.get("persona", "通用朋友"),
                                               w["display"], ctx, standalone,
                                               asker, profile=profile,
                                               self_profile=self_profile)
            if not reply:
                reply = "这个我手头暂时没查到准确资料，已帮你记下反馈给群里小伙伴啦～"
            self.send_quote(w["display"], reply, target_name=asker, target_content=raw_msg_content)
            self.notify_admins(f"[待沉淀] {w['display']} {asker}: {standalone[:80]}")

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
        self._sink_to_kb(w)
        if not self._judge_no_at(w, context):
            return
        reply = self._no_at_answer(w, context)
        if not reply:
            return
        if self.send(w["display"], reply, "no_at"):
            marks[w["user"]] = time.time()

    def _sink_to_kb(self, w):
        """把上次水位线之后的新群消息（_SENSITIVE 过滤后）上传知识库；失败静默降级。"""
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
            title = f"{gname} 聊天沉淀 {time.strftime('%Y%m%d_%H%M')}"
            md = ["# " + title, "", f"来源群：{gname}", ""]
            md += ["- " + t["line"] for t in lines]
            self.kb_writer.upload_markdown(title, "\n".join(md))
            self.store.set_kb_seq(gid, lines[-1]["seq"])
            log.info("KB 沉淀 %s：%d 条", gname, len(lines))
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

    def _no_at_answer(self, w, context):
        """生成免@回复，prompt 里并入知识库检索参考；检索失败就无参考作答。"""
        ref = ""
        try:
            ref = self.kb.search("\n".join(context[-5:]), top_k=3)
        except Exception as e:
            log.warning("KB 检索失败(%s)，本次无参考作答: %s", w["display"], e)
        try:
            return self.personas.context_reply(
                w["cfg"].get("persona", "群管家-热心"), w["display"], context, ref)
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
            return ("命令：#状态 #暂停 #恢复 #开 <名> #关 <名> #重载 #待审\n"
                    "（联系人/群名单与开关在 bot_config.yaml 或网页配置）")
        if name == "状态":
            ups = int(time.time() - self.state["start"])
            ws = ", ".join(f'{w["display"]}({"群" if w["kind"]=="group" else "私"})'
                           for w in self.watch) or "无"
            return (f"运行 {ups//60} 分钟，已发送 {self.state['sent']} 条，"
                    f"{'已暂停' if self.state['paused'] else '正常'}\n监控: {ws}")
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
                return f"已{'' if name=='开' else '停用'} {target}"
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
        return f"未知命令 #{name}，#帮助 查看"
