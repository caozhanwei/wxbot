# -*- coding: utf-8 -*-
"""微信智能助手 PC 版入口。

用法：
  python run_bot.py                 # 桌面窗口启动（打包安装后的默认形态）
  python run_bot.py --nogui         # 纯控制台启动（含 Web 状态页）
  python run_bot.py --list          # 列最近会话（含群 id），用于填 bot_config.yaml
  python run_bot.py --check         # 自检：微信窗口/自身信息/LLM 连通
  python run_bot.py --test-reply <联系人> <文本>   # 模拟一条来信走完整回复链路（建议用 文件传输助手 测试）
"""
import logging
import os
import sys

from wxbot import paths

LOG_FMT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"


def _guard_stdio():
    # windowed exe 下 stdout/stderr 为 None，print/logging 会炸，补成 devnull
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _init_log():
    # 注意：wechatauto 导入时会清空 root handlers 只留自己的控制台输出，
    # 所以必须先导入 wxbot.bot（拉起 wechatauto），再装我们的文件日志。
    logging.basicConfig(level=logging.INFO, format=LOG_FMT, force=True,
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(paths.resolve("wxbot.log"),
                                                      encoding="utf-8")])


def do_list():
    from wxbot.driver import WxDriver
    d = WxDriver()
    rows = d.list_recent_chats(80)
    groups = [r for r in rows if r["is_group"]]
    priv = [r for r in rows if not r["is_group"]]
    print("== 最近私聊 ==")
    for r in priv[:40]:
        print(f'  - id: {r["id"]:<34} display: {r["display"]}')
    print("== 最近的群（id 填入 bot_config.yaml 的 groups）==")
    for r in groups:
        print(f'  - id: {r["id"]:<34} display: {r["display"]}')
    if not groups:
        print("  （最近会话里没有群）")


def do_check():
    from wxbot.config import BotConfig
    from wxbot.driver import WxDriver
    cfg = BotConfig(paths.default_config())
    d = WxDriver()
    print(f"微信窗口 OK，自身: {d.self_name} ({d.self_id})")
    from llm import LLM
    lc = cfg.llm_cfg
    llm = LLM(lc.get("provider", "custom"), lc.get("api_key", ""),
              lc.get("model", ""), base_url=lc.get("base_url", ""),
              timeout=int(lc.get("timeout", 60)))
    print("LLM:", llm.chat("回复ok两个字", "ping", temperature=0.1))
    kbc = cfg.kb_cfg
    if kbc.get("url"):
        from answer import McpKBClient
        kb = McpKBClient({"mcp": kbc})
        try:
            ans, hit, grounded = kb.ask("ping 连通性测试")
            print(f"KB 连通: hit={hit} grounded={grounded} ans={ans[:40]!r}")
        except Exception as e:
            print(f"KB 异常（不影响私聊功能）: {e}")
    print(f"监控: {len([c for c in cfg.contacts if c.get('enabled')])} 个联系人, "
          f"{len([g for g in cfg.groups if g.get('enabled')])} 个群, dry_run={cfg.dry_run}")


def do_test_reply(target, text):
    """模拟 target 发来 text，走人设回复链路（真实发送，测试请用 文件传输助手）。"""
    from wxbot.config import BotConfig
    from wxbot.driver import WxDriver
    from wxbot.personas import PersonaEngine
    cfg = BotConfig(paths.default_config())
    d = WxDriver()
    uid = target if cfg.contact_by_user(target) else d.resolve_id(target)
    contact = cfg.contact_by_user(uid)
    if not contact:
        print(f"bot_config.yaml 里没有 {target}，请先加入 contacts（建议先只测 文件传输助手）")
        return 1
    display = contact.get("display") or uid
    ctx = d.build_context(uid, int(cfg.reply_cfg.get("history_limit", 16)))
    ctx.append(f"对方: {text}")
    pe = PersonaEngine(cfg)
    reply = pe.chat_reply(contact.get("persona", "通用朋友"), display, ctx[:-1], text)
    print("生成回复:", reply)
    ok, why = d.send_text(display, reply)
    print("发送:", "成功" if ok else f"失败 {why}")
    return 0 if ok else 2


def main():
    _guard_stdio()
    import wxbot.bot  # noqa: F401 预导入：让 wechatauto 先完成它的 root handlers 清理
    _init_log()
    args = sys.argv[1:]
    if args[:1] == ["--list"]:
        do_list()
    elif args[:1] == ["--check"]:
        do_check()
    elif args[:2] and args[0] == "--test-reply":
        if len(args) < 3:
            print(__doc__)
            return 1
        return do_test_reply(args[1], args[2])
    elif not args:
        from wxbot.app import run_gui
        run_gui()
    elif args[:1] == ["--nogui"]:
        from wxbot.bot import Bot
        Bot(paths.default_config()).run()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
