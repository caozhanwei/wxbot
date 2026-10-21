# -*- coding: utf-8 -*-
"""管理员命令（群内或私聊，# 前缀，仅 admins 可用）。

#帮助              命令列表
#状态              运行统计
#待审              待审核问答对列表
#通过 <qid>        通过问答对（并触发回灌）
#拒绝 <qid>        拒绝问答对
#抽取 <qid>        从某条 open 问题手动抽取问答对
#日报              立即发一份日报
#暂停 / #恢复      暂停/恢复问答挖掘与违禁词检查
#导出              把已通过问答对导出成 md（供知识库管理员导入团队库）
"""
import logging

log = logging.getLogger("admin")

_HELP = """可用命令：
#状态  运行统计
#待审  待审核问答对
#通过 <id> / #拒绝 <id>
#抽取 <问题id>  从 open 问题手动抽取问答对
#日报  立即发日报
#暂停 / #恢复
#导出  把已通过问答对导出成 md 文件"""


class AdminCommands:
    def __init__(self, config, db, wechat, router, tracker, gmgr, ingest):
        self.config = config
        self.db = db
        self.wechat = wechat
        self.router = router
        self.tracker = tracker
        self.gmgr = gmgr
        self.ingest = ingest

    def handle(self, msg):
        parts = msg.content.strip().split()
        cmd = parts[0].lstrip("#").lower()
        args = parts[1:]
        reply_to = msg.chat_id
        try:
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler:
                out = handler(args)
            elif cmd in ("help", "帮助", "?"):
                out = _HELP
            else:
                out = f"未知命令 {cmd}\n{_HELP}"
        except Exception as e:
            log.exception("命令执行失败")
            out = f"命令执行失败: {e}"
        if out:
            self.wechat.send_text(out, reply_to)
        self.db.audit("admin", cmd, " ".join(args)[:100])

    def cmd_状态(self, args=None):
        s = self.db.stats_today()
        mode = "dry-run" if self.config["dry_run"] else "live"
        paused = "已暂停" if self.router.paused else "运行中"
        return (f"模式: {mode} / {paused}\n"
                f"近24h消息: {s['msgs_24h']}\n新增问题: {s['new_questions_24h']}（未解决 {s['open_questions']}）\n"
                f"待审核问答对: {s['pending_qa']}")

    def cmd_待审(self, args=None):
        pairs = self.db.qa_pairs_by_status("pending", limit=10)
        if not pairs:
            return "没有待审核的问答对"
        out = ["待审核问答对（#通过 id / #拒绝 id）:"]
        for p in pairs:
            out.append(f"#{p['id']}\nQ: {p['q_text'][:80]}\nA: {p['a_text'][:80]}\n")
        return "\n".join(out)

    def cmd_通过(self, args):
        if not args or not args[0].isdigit():
            return "用法: #通过 <问答对id>"
        qid = int(args[0])
        pair = self.db.qa_pair(qid)
        if not pair:
            return f"问答对 #{qid} 不存在"
        if pair["status"] != "pending":
            return f"问答对 #{qid} 当前状态 {pair['status']}，只有 pending 可通过"
        self.db.set_qa_status(qid, "approved", "admin")
        done, total = self.ingest.ingest_approved()
        if total:
            return f"已通过 #{qid}，并导出到 exports/（{done}/{total}），由知识库管理员导入团队库后即可命中"
        return f"已通过 #{qid}（导出暂未执行，可 #导出 手动触发）"

    def cmd_拒绝(self, args):
        if not args or not args[0].isdigit():
            return "用法: #拒绝 <问答对id>"
        qid = int(args[0])
        if not self.db.qa_pair(qid):
            return f"问答对 #{qid} 不存在"
        self.db.set_qa_status(qid, "rejected", "admin")
        return f"已拒绝 #{qid}"

    def cmd_抽取(self, args):
        if not args or not args[0].isdigit():
            return "用法: #抽取 <问题id>（#待审 之外用 #状态 查看未解决问题）"
        qa_id, out = self.tracker.approve_question(int(args[0]))
        return out

    def cmd_日报(self, args=None):
        return self.gmgr.daily_report()

    def cmd_暂停(self, args=None):
        self.router.paused = True
        return "已暂停问答挖掘与违禁词检查（@答疑不受影响）"

    def cmd_恢复(self, args=None):
        self.router.paused = False
        return "已恢复"

    def cmd_回灌(self, args=None):
        return self.cmd_导出()

    def cmd_导出(self, args=None):
        done, total = self.ingest.ingest_approved()
        if not total:
            return "没有待导出的问答对"
        return f"已导出 {done} 对到 exports/ 目录，请知识库管理员导入团队问答库"
