# -*- coding: utf-8 -*-
"""主编排：轮询所有已配置聊天 → 私聊人设回复 / 群答疑 / 群管理 / 管理员命令。"""
import logging
import random
import time

from answer import McpKBClient, KBError

from . import group_logic, paths
from .config import BotConfig
from .driver import WxDriver
from .personas import PersonaEngine
from .store import Store

log = logging.getLogger("wxbot")


class Bot:
    def __init__(self, cfg_path="bot_config.yaml", with_web=True, cfg=None, state=None,
                 driver=None):
        self.cfg = cfg or BotConfig(cfg_path)
        self.store = Store(paths.resolve(self.cfg.db_file))
        self.driver = driver or WxDriver()
        self.personas = PersonaEngine(self.cfg)
        self._kb = None
        self.with_web = with_web
        # GUI 模式下与外壳共享同一对象，网页端看到的配置/暂停状态才是真实的
        self.state = state or {}
        self.state.setdefault("paused", False)
        self.state.setdefault("sent", 0)
        self.state.setdefault("start", time.time())
        self.state.setdefault("kb_fail_ts", 0)
        self.state.setdefault("fails", {})
        self.watch = []

    # ---------- 基础 ----------
    @property
    def kb(self):
        if self._kb is None:
            self._kb = McpKBClient({"mcp": self.cfg.kb_cfg})
        return self._kb

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
        """统一发送出口：dry_run / 计数 / 落日志。"""
        text = str(text)
        self.store.log(kind, display, "", text)
        if self.cfg.dry_run:
            log.info("[dry] → %s: %s", display, text[:60])
            return True
        ok, why = self.driver.send_text(display, text)
        if ok:
            self.state["sent"] += 1
        else:
            log.warning("发送失败 → %s: %s", display, why)
        return ok

    def notify_admins(self, text):
        for a in self.cfg.admins:
            if a and a != self.driver.self_id:
                self.send(self.driver.display_of(a), text, "notify")

    # ---------- 主循环 ----------
    def run(self):
        self.build_watch()
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
        rc = self.cfg.reply_cfg
        time.sleep(float(rc.get("quiesce_sec", 5)))          # 合并连发
        more, w["since"] = self.driver.poll(w["user"], w["since"])
        for m in more:
            if (not m["is_self"] and m["type"] == "文本"
                    and not m["content"].startswith("<?xml")):
                texts.append(m)
        last = texts[-1]["content"]
        log.info("私聊 %s: %s", w["display"], last[:60])
        time.sleep(random.uniform(*[float(x) for x in rc.get("human_delay", (2, 5))]))
        try:
            ctx = self.driver.build_context(w["user"], int(rc.get("history_limit", 16)))
            reply = self.personas.chat_reply(w["cfg"].get("persona", "通用朋友"),
                                             w["display"], ctx, last)
        except Exception as e:
            log.warning("LLM 失败(%s): %s", w["display"], e)
            self._rewind_or_drop(w, texts)
            return
        if not reply:
            self._rewind_or_drop(w, texts)
            return
        if self.send(w["display"], reply, "reply"):
            self.state["fails"][w["user"]] = 0
        else:
            self._rewind_or_drop(w, texts)

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
                self._group_qa(w, m)
            elif mg.get("forbidden_words"):
                word = group_logic.forbidden_hit(m["content"], mg["forbidden_words"])
                if word:
                    self._warn(w, m, word)

    def _welcome(self, w, name):
        text = ((w["cfg"].get("manage") or {}).get("welcome_text")
                or "欢迎 {name} 入群！有问题可以直接 @我 提问～")
        self.send(w["display"], text.format(name=name), "welcome")

    def _group_qa(self, w, m):
        g = w["cfg"]
        qa = g.get("qa") or {}
        names = self.driver.mention_names(g)
        question = group_logic.strip_at(m["content"], names)
        asker = m["sender_display"] or m["sender"]
        if not question:
            self.send(w["display"], f"@{asker} 我在的，@我 直接说问题就行～", "qa")
            return
        log.info("群答疑 %s: %s", w["display"], question[:60])
        try:
            answer, hit, grounded = self.kb.ask(question, w["display"])
        except KBError as e:
            log.warning("KB 调用失败: %s", e)
            if time.time() - self.state["kb_fail_ts"] > 600:
                self.state["kb_fail_ts"] = time.time()
                self.notify_admins(f"[告警] 知识库调用失败: {e}")
            if qa.get("llm_fallback"):
                try:
                    rc = self.cfg.reply_cfg
                    ctx = self.driver.build_context(w["user"], int(rc.get("history_limit", 16)))
                    reply = self.personas.chat_reply(g.get("persona", "通用朋友"),
                                                     w["display"], ctx, question)
                    self.send(w["display"], f"@{asker} {reply}", "qa")
                except Exception as ee:
                    log.warning("LLM 兜底也失败: %s", ee)
            return
        if hit:
            self.send(w["display"], f"@{asker} {answer}", "qa")
        elif grounded and qa.get("grounded_reply", True):
            self.store.add_qa_pending(w["display"], asker, question, answer)
            self.send(w["display"], f"@{asker} 知识库没有完全正中的答案，以下是相关内容供参考：\n{answer}", "qa")
        else:
            self.store.add_qa_pending(w["display"], asker, question)
            self.send(w["display"], f"@{asker} 知识库里暂时没有这个问题的答案，已记录，会有同学跟进～", "qa")
            self.notify_admins(f"[待沉淀] {w['display']} {asker}: {question[:80]}")

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
            self.build_watch()
            return "配置已重载"
        if name == "待审":
            rows = self.store.qa_pending(10)
            if not rows:
                return "暂无待沉淀问题"
            return "\n".join(f'#{r["id"]} [{r["grp"]}] {r["questioner"]}: {r["question"][:40]}'
                             for r in rows)
        return f"未知命令 #{name}，#帮助 查看"
