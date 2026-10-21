# -*- coding: utf-8 -*-
"""端到端管线（MockWeChat 回放）：答疑 / 挖掘 / 审核 / 回灌 / 违禁词 / 欢迎。"""
def _sent(stack):
    return stack["wechat"].wait_sent()


def _clear(stack):
    with stack["wechat"].sent_lock:
        stack["wechat"].sent[:] = []


def test_at_bot_answer_hit(stack):
    stack["wechat"].inject(sender="user_a", room="room_1", sender_name="小王",
                           content="NETLIST_FILE 怎么用", is_at_bot=True)
    sent = _sent(stack)
    assert any("NETLIST_FILE" in t for t, r, a in sent), sent
    assert any(a == "user_a" for t, r, a in sent)          # @了提问者


def test_at_bot_answer_miss_recorded(stack, db):
    stack["wechat"].inject(sender="user_a", room="room_1", sender_name="小王",
                           content="怎么配置多工艺角 mcf 文件", is_at_bot=True)
    sent = _sent(stack)
    assert any("没有这个问题的答案" in t or "连不上" in t for t, r, a in sent), sent
    # 未命中记录为问题
    qs = db.questions_by_status("open")
    assert any("mcf" in q["text"] for q in qs)
    # 管理员被私信提醒
    assert any(r == "admin_wxid" for t, r, a in sent)


def test_mining_full_cycle(stack, db):
    # 1. 群友提问（非@，普通消息）
    stack["wechat"].inject(sender="user_b", room="room_1", sender_name="小李",
                           content="请问如何指定输出网表文件？")
    _clear(stack)
    # 2. 他人回复
    stack["wechat"].inject(sender="user_c", room="room_1", sender_name="老张",
                           content="用 NETLIST_FILE 命令，写在 cmd 文件第三节")
    _clear(stack)
    # 3. 提问者感谢 → 触发抽取
    stack["wechat"].inject(sender="user_b", room="room_1", sender_name="小李", content="谢谢")
    pairs = db.qa_pairs_by_status("pending")
    assert len(pairs) == 1
    p = pairs[0]
    assert p["q_text"] == "如何指定输出网表文件？"     # FakeLLM 抽取结果
    assert p["a_text"].startswith("使用 NETLIST_FILE")
    assert p["answerer_wxid"] == "user_c"
    # 问题闭环
    qs = db.questions_by_status("answered")
    assert any("网表" in q["text"] for q in qs)
    # 4. 管理员审核通过 → 导出到 exports/，状态 exported
    stack["wechat"].inject(sender="admin_wxid", room="room_1", sender_name="管理员",
                           content="#通过 %d" % p["id"])
    assert db.qa_pair(p["id"])["status"] == "exported"
    sent = _sent(stack)
    assert any("已通过" in t and str(p["id"]) in t for t, r, a in sent), sent


def test_reply_then_expire(stack, db):
    stack["wechat"].inject(sender="user_b", room="room_1", sender_name="小李",
                           content="有人知道 vvspace 的表怎么填吗？")
    # 无回复、无感谢，直接来一个新问题 → 旧的置 expired
    stack["wechat"].inject(sender="user_d", room="room_1", sender_name="小赵",
                           content="请问 mcf 文件支持哪些工艺角？")
    expired = db.questions_by_status("expired")
    assert any("vvspace" in q["text"] for q in expired)
    opens = db.questions_by_status("open")
    assert any("mcf" in q["text"] for q in opens)


def test_forbidden_word(stack, db):
    stack["wechat"].inject(sender="user_e", room="room_1", sender_name="路人",
                           content="低价接单，需要加微信互推的私聊")
    sent = _sent(stack)
    assert any(r == "admin_wxid" and "违禁词" in t for t, r, a in sent), sent


def test_welcome_on_join(stack):
    stack["wechat"].inject(sender="system", room="room_1", mtype=10000,
                           content='"新人甲"通过扫描二维码加入群聊')
    sent = _sent(stack)
    assert any("新人甲" in t and "欢迎" in t for t, r, a in sent), sent


def test_admin_commands(stack):
    stack["wechat"].inject(sender="admin_wxid", room="room_1", sender_name="管理员", content="#状态")
    sent = _sent(stack)
    assert any("模式" in t for t, r, a in sent), sent
    _clear(stack)
    stack["wechat"].inject(sender="admin_wxid", room="room_1", sender_name="管理员", content="#帮助")
    sent = _sent(stack)
    assert any("#通过" in t for t, r, a in sent), sent


def test_non_admin_cannot_command(stack):
    stack["wechat"].inject(sender="user_a", room="room_1", sender_name="小王", content="#状态")
    # 非 admin 的 # 消息走普通挖掘，不会触发命令回复
    sent = _sent(stack)
    assert not any("模式:" in t for t, r, a in sent), sent


def test_unmonitored_room_ignored(stack, db):
    stack["wechat"].inject(sender="user_x", room="room_other", content="请问这个问题会被处理吗？")
    msgs = [m for m in db.recent_messages("room_other", 0)]
    assert msgs == []          # adapter 层就过滤了
