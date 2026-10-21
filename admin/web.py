# -*- coding: utf-8 -*-
"""轻量审核页：http://127.0.0.1:8777 列出待审核问答对，一键通过/拒绝。仅监听本机。"""
import html
import json
import logging

from flask import Flask, request

log = logging.getLogger("web")

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>微信群助手 · 问答审核</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;background:#f7f8fa}
h1{font-size:20px} .card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin:10px 0}
.q{font-weight:600;margin:0 0 6px}.a{white-space:pre-wrap;color:#374151;margin:0}
.meta{color:#9ca3af;font-size:12px;margin-top:8px}
form{display:inline}button{margin-right:6px;padding:4px 12px;border-radius:6px;border:1px solid #d1d5db;cursor:pointer}
.ok{background:#059669;color:#fff;border-color:#059669}.no{background:#fff}
.tab{margin:12px 0}a{margin-right:12px}
</style></head><body>
<h1>微信群助手 · 问答审核 {extra}</h1>
<div class="tab"><a href="/?status=pending">待审核</a><a href="/?status=approved">已通过</a>
<a href="/?status=exported">已导出</a><a href="/?status=rejected">已拒绝</a><a href="/?status=openq">未解决问题</a></div>
{body}
</body></html>"""


def create_app(db, ingest, tracker):
    app = Flask(__name__)

    def render(status, body):
        return _PAGE.replace("{extra}", f"· {status}").replace("{body}", body)

    def render_pairs(status):
        pairs = db.qa_pairs_by_status(status, limit=100)
        if not pairs:
            return render(status, "<p>空</p>")
        cards = []
        for p in pairs:
            src = ""
            try:
                srcs = json.loads(p["source_msgs"] or "[]")
                src = html.escape(" | ".join(s[:40] for s in srcs[1:3]))
            except Exception:
                pass
            btn = ""
            if status == "pending":
                btn = (f'<form method="post" action="/review"><input type="hidden" name="id" value="{p["id"]}">'
                       f'<input type="hidden" name="action" value="approve">'
                       f'<button class="ok">通过并导出</button></form>'
                       f'<form method="post" action="/review"><input type="hidden" name="id" value="{p["id"]}">'
                       f'<input type="hidden" name="action" value="reject"><button class="no">拒绝</button></form>')
            cards.append(
                f'<div class="card"><p class="q">#{p["id"]} Q: {html.escape(p["q_text"])}</p>'
                f'<p class="a">A: {html.escape(p["a_text"])}</p>'
                f'<p class="meta">来源消息: {src or "-"} · 审核人: {html.escape(p["reviewed_by"] or "-")}</p>{btn}</div>')
        return render(status, "".join(cards))

    @app.get("/")
    def index():
        status = request.args.get("status", "pending")
        if status == "openq":
            qs = db.questions_by_status("open", limit=100)
            if not qs:
                return render("未解决问题", "<p>空</p>")
            cards = [f'<div class="card"><p class="q">#{q["id"]} {html.escape(q["text"])}</p>'
                     f'<p class="meta">来自 {html.escape(q["questioner"])} · 可在群里发 “#抽取 {q["id"]}” 人工成对</p></div>'
                     for q in qs]
            return render("未解决问题", "".join(cards))
        return render_pairs(status)

    @app.post("/review")
    def review():
        qid = int(request.form.get("id", "0"))
        action = request.form.get("action")
        pair = db.qa_pair(qid)
        if not pair or pair["status"] != "pending":
            return "状态已变更，<a href='/'>返回</a>"
        if action == "approve":
            db.set_qa_status(qid, "approved", "web")
            done, total = ingest.ingest_approved()
            db.audit("web", "approve", f"#{qid} ingest {done}/{total}")
        else:
            db.set_qa_status(qid, "rejected", "web")
            db.audit("web", "reject", f"#{qid}")
        return '已处理 <a href="/">返回</a>'

    return app


def run_web(config, db, ingest, tracker):
    app = create_app(db, ingest, tracker)
    port = int(config["web"]["port"])
    log.info("审核页启动 http://127.0.0.1:%d", port)
    app.run(host="127.0.0.1", port=port, debug=False)
