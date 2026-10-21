# -*- coding: utf-8 -*-
"""微信群助手入口。

用法：
  python main.py            正式运行（需本机微信已登录，版本与 wcferry 匹配）
  python main.py --check    只初始化并打印微信/wcferry 版本与自身 wxid，用于验证环境、取 room id
  python main.py --mock     无微信开发/演示模式（MockWeChat，消息需用 tests 驱动，一般不用）
  python main.py --no-web   不启动审核页
"""
import argparse
import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from db import open_db  # noqa: E402
from llm import LLM  # noqa: E402
from answer import McpKBClient  # noqa: E402
from mining.tracker import QuestionTracker  # noqa: E402
from ingest import Ingestor  # noqa: E402
from router import Router  # noqa: E402
from admin.commands import AdminCommands  # noqa: E402
from gmgr import GroupManager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(Path(__file__).parent / "assistant.log", encoding="utf-8")])
log = logging.getLogger("main")


def load_config():
    with open(Path(__file__).parent / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build(config, db, mock=False):
    if mock:
        from adapter import MockWeChat
        wechat = MockWeChat(config, db)
    else:
        from adapter import WeChatAdapter
        wechat = WeChatAdapter(config, db)
    kb = McpKBClient(config)
    llm = LLM(config["llm"]["provider"], config["llm"]["api_key"], config["llm"]["model"],
              base_url=config["llm"].get("base_url", ""))
    gmgr = GroupManager(config, db, wechat)
    # router/tracker/admin 互相引用，先占位再注入
    router = Router(config, db, wechat, kb, None, None, gmgr)
    ingest = Ingestor(config, db)

    def notify_new_qa(qa_id):
        pair = db.qa_pair(qa_id)
        for admin in config["admins"]:
            wechat.send_text(f"[新问答对待审 #{qa_id}]\nQ: {pair['q_text'][:80]}\nA: {pair['a_text'][:80]}\n"
                             f"群内回复“#通过 {qa_id}”或在审核页处理", admin)

    tracker = QuestionTracker(config, db, llm, on_new_qa=notify_new_qa)
    admin = AdminCommands(config, db, wechat, router, tracker, gmgr, ingest)
    router.tracker = tracker
    router.admin = admin
    return wechat, kb, tracker, admin, gmgr, ingest, router


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只验证环境，不进消息循环")
    parser.add_argument("--mock", action="store_true", help="无微信开发模式")
    parser.add_argument("--no-web", action="store_true", help="不启动审核页")
    args = parser.parse_args()

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    config = load_config()
    db = open_db()
    wechat, kb, tracker, admin, gmgr, ingest, router = build(config, db, mock=args.mock)

    log.info("dry_run=%s rooms=%s admins=%s", config["dry_run"], config["rooms"], config["admins"])
    if not kb.ready:
        log.warning("知识库 MCP 未配置完整，@答疑将降级为记录问题")
    if not config["llm"]["api_key"]:
        log.warning("llm api_key 未配置，问答对抽取将回退为原文拼接")

    if args.check:
        if args.mock:
            log.info("[mock] self_wxid=%s", wechat.self_wxid)
        else:
            log.info("微信版本: %s", wechat.wcf.get_wechat_version())
            log.info("自身 wxid: %s", wechat.self_wxid)
            log.info("在监控的群里发条消息，assistant.log / 控制台会打印 room id，回填 config.yaml 的 rooms")
        return

    wechat.start(router.dispatch)
    log.info("消息循环已启动")

    # 定时日报
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler()
    sched.add_job(gmgr.daily_report, "cron",
                  hour=int(config["report"]["cron_hour"]),
                  minute=int(config["report"]["cron_minute"]),
                  jitter=300)
    sched.start()

    # 审核页
    if config["web"]["enabled"] and not args.no_web:
        from admin.web import run_web
        threading.Thread(target=run_web, args=(config, db, ingest, tracker), daemon=True).start()

    log.info("微信群助手已就绪。Ctrl+C 退出。")
    try:
        threading.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        log.info("退出")
        wechat.sendq.stop()
        sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
