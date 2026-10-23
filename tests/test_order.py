# -*- coding: utf-8 -*-
"""R3 群秩序自动化：刷屏告警（每成员每小时一次）/ 退群预警 / 新人引导指路主题库。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_console import _mk_bot


def _group_w(b):
    return [x for x in b.watch if x["kind"] == "group"][0]


def _msg(content, sender="wxid_a", name="阿强", mtype="文本", ts=None, seq=None):
    return {"type": mtype, "is_self": False, "sender": sender,
            "sender_display": name, "seq": seq or int(time.time()),
            "ts": ts or time.time(), "content": content}


def _notify_logs(b):
    return [r for r in b.store.recent_logs(80) if r["kind"] == "notify"]


# ---------- 刷屏检测 ----------

def test_flood_alerts_admins_once_per_hour(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    burst = [_msg(f"刷屏第{i}条", seq=100 + i) for i in range(12)]
    b._on_group_msgs(w, burst)
    logs = _notify_logs(b)
    assert len(logs) == 1 and "刷屏" in logs[0]["detail"] and "阿强" in logs[0]["detail"]
    b._on_group_msgs(w, [_msg(f"又刷{i}", seq=200 + i) for i in range(12)])
    assert len(_notify_logs(b)) == 1                    # 一小时内不重复告警


def test_normal_pace_no_alert(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg(f"正常聊天{i}", seq=100 + i) for i in range(5)])
    assert not [l for l in _notify_logs(b) if "刷屏" in l["detail"]]


# ---------- 退群预警 ----------

def test_leave_and_remove_warning(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg('"张三"退出了群聊', mtype="系统消息", seq=301)])
    b._on_group_msgs(w, [_msg('"李四"被"王五"移出了群聊', mtype="系统消息", seq=302)])
    logs = [l for l in _notify_logs(b) if "群秩序" in l["detail"]][::-1]
    assert len(logs) == 2
    assert "张三" in logs[0]["detail"] and "李四" in logs[1]["detail"]


def test_join_no_warning(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg('"张三"通过扫描二维码加入了群聊',
                              mtype="系统消息", seq=303)])
    assert not [l for l in _notify_logs(b) if "群秩序" in l["detail"]]


# ---------- 新人引导指路主题库 ----------

def test_welcome_points_to_themes(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b.store.add_mined("111@chatroom", [("怎么续费物业费", "去物业处缴"),
                                       ("门禁卡哪里办", "物业前台"),
                                       ("地铁几号线", "12号线"),
                                       ("垃圾站在哪", "东门")])
    for r, t in zip(b.store.mined_pairs("111@chatroom"),
                    ["交通出行", "物业手续", "物业手续", "物业手续"]):
        b.store.set_theme(r["id"], t)
    b._welcome(w, "新人小王")
    logs = [r for r in b.store.recent_logs(20) if r["kind"] == "welcome"]
    assert logs and "新人小王" in logs[0]["detail"]
    assert "物业手续" in logs[0]["detail"] and "交通出行" in logs[0]["detail"]


def test_welcome_without_themes_plain(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._welcome(w, "新人小李")
    logs = [r for r in b.store.recent_logs(20) if r["kind"] == "welcome"]
    assert logs and "新人小李" in logs[0]["detail"]
    assert "常见话题" not in logs[0]["detail"]           # 没主题库不加尾巴


# ---------- 广告号识别（只告警，踢人走 #踢 人工确认） ----------

def test_ads_markers_basics():
    from wxbot import group_logic
    ms = group_logic.ads_markers("加微信领优惠券 私聊我 http://x.cn/abcd")
    assert "链接" in ms and "加微信" in ms and "优惠券" in ms and "私聊我" in ms
    assert group_logic.ads_markers("正常聊天一句话") == []
    assert group_logic.ads_markers("") == []
    assert group_logic.ads_markers("今天群里团购白菜", extra=("团购",)) == ["团购"]


def test_new_member_ads_alerts_once(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg('"营销号"通过扫描二维码加入了群聊', mtype="系统消息", seq=401)])
    b._on_group_msgs(w, [_msg("加微信领优惠券 私聊我秒到账 http://x.cn/a",
                              name="营销号", sender="wxid_ad", seq=402)])
    logs = [l for l in _notify_logs(b) if "广告嫌疑" in l["detail"]]
    assert len(logs) == 1
    assert "营销号" in logs[0]["detail"] and "#踢" in logs[0]["detail"]
    # 再发也不重复告警
    b._on_group_msgs(w, [_msg("加微信返利 http://y.cn/b", name="营销号",
                              sender="wxid_ad", seq=403)])
    assert len([l for l in _notify_logs(b) if "广告嫌疑" in l["detail"]]) == 1


def test_old_member_ads_ignored(tmp_path):
    """老人发广告不走新成员告警（避免误伤正常群友发链接）。"""
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg("加微信领优惠券 私聊我 http://x.cn/a", seq=404)])
    assert not [l for l in _notify_logs(b) if "广告嫌疑" in l["detail"]]


def test_single_marker_no_alert(tmp_path):
    """单个特征（比如只发个链接）可能是正常分享，不告警。"""
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    b._on_group_msgs(w, [_msg('"小王"加入了群聊', mtype="系统消息", seq=405)])
    b._on_group_msgs(w, [_msg("看这个 http://x.cn/ok", name="小王", sender="wxid_w", seq=406)])
    assert not [l for l in _notify_logs(b) if "广告嫌疑" in l["detail"]]


def test_ads_watch_off(tmp_path):
    b = _mk_bot(tmp_path)
    b.build_watch()
    w = _group_w(b)
    w["cfg"].setdefault("manage", {}).setdefault("order", {})["ads_watch"] = False
    b._on_group_msgs(w, [_msg('"营销号"加入了群聊', mtype="系统消息", seq=407)])
    b._on_group_msgs(w, [_msg("加微信领优惠券 私聊我 http://x.cn/a",
                              name="营销号", sender="wxid_ad", seq=408)])
    assert not [l for l in _notify_logs(b) if "广告嫌疑" in l["detail"]]
