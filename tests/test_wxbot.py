# -*- coding: utf-8 -*-
"""wxbot 单元测试：群逻辑、配置、人设 prompt、存储。"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import group_logic
from wxbot.config import BotConfig
from wxbot.store import Store


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "bot_config.yaml"
    p.write_text("""
app: {dry_run: true}
llm: {provider: custom, base_url: "http://x/v1", api_key: k, model: m, temperature: 0.5}
personas:
  测试人设: {style: 简短幽默, background: 老同事}
contacts:
  - {id: wxid_testuser, display: 李明, persona: 测试人设, enabled: true}
  - {id: wxid_2, display: 某人, persona: 不存在的人设, enabled: false}
groups:
  - id: "111@chatroom"
    display: 测试群
    enabled: true
    qa: {enabled: true, grounded_reply: true}
    manage: {welcome: true, forbidden_words: [广告, 刷单], warn_persona: 群管家-热心, warn_threshold: 2}
""", encoding="utf-8")
    return BotConfig(str(p))


# ---------- group_logic ----------
def test_parse_group_content():
    assert group_logic.parse_group_content("wxid_abc: 你好") == ("wxid_abc", "你好")
    assert group_logic.parse_group_content("wxid_abc:你好") == ("wxid_abc", "你好")
    assert group_logic.parse_group_content("普通消息") == (None, "普通消息")


def test_at_bot_and_strip():
    names = ["群助手", "占伟"]
    assert group_logic.at_bot("@群助手 NETLIST_FILE 怎么配", names)
    assert group_logic.at_bot("问一下 @占伟 这个参数", names)
    assert not group_logic.at_bot("@群助手2 别误会", names)      # 前缀相同但更长
    assert not group_logic.at_bot("大家看看这个问题", names)
    assert group_logic.strip_at("@群助手  NETLIST_FILE 怎么配", names) == "NETLIST_FILE 怎么配"


def test_join_name():
    assert group_logic.join_name('"张三"通过扫描二维码加入群聊') == "张三"
    assert group_logic.join_name('"张三"通过扫描xxx二维码加入了群聊') == "张三"
    assert group_logic.join_name('邀请"李四"加入了群聊') == "李四"
    assert group_logic.join_name("今天天气不错") is None


def test_forbidden_hit():
    assert group_logic.forbidden_hit("加我微信买广告位", ["广告"]) == "广告"
    assert group_logic.forbidden_hit("正常聊天", ["广告", "刷单"]) is None


# ---------- config ----------
def test_config(cfg):
    assert cfg.dry_run is True
    assert cfg.contact_by_user("wxid_testuser")["display"] == "李明"
    assert cfg.contact_by_user("wxid_2")["enabled"] is False
    g = cfg.group_by_user("111@chatroom")
    assert g["manage"]["warn_threshold"] == 2
    assert cfg.persona("测试人设")["style"] == "简短幽默"
    assert cfg.persona("不存在的人设")["style"]              # 兜底人设
    assert cfg.persona_names == ["测试人设"]


def test_hot_reload(cfg):
    assert cfg.maybe_reload() is False
    with open(cfg.path, "a", encoding="utf-8") as f:
        f.write("\nadmins: [wxid_admin]\n")
    import time
    time.sleep(0.05)
    assert cfg.maybe_reload() is True
    assert cfg.admins == ["wxid_admin"]


# ---------- personas ----------
def test_persona_system_prompt(cfg):
    from wxbot.personas import PersonaEngine
    pe = PersonaEngine(cfg)
    s = pe.system_for("测试人设", "李明")
    assert "李明" in s and "简短幽默" in s and "老同事" in s
    assert "只输出一条" in s


def test_warn_message_fallback(cfg, monkeypatch):
    from wxbot.personas import PersonaEngine
    pe = PersonaEngine(cfg)

    class _Boom:
        def chat(self, *a, **kw):
            raise RuntimeError("down")

    monkeypatch.setattr(type(pe), "llm", property(lambda self: _Boom()))
    msg = pe.warn_message("群管家-热心", "张三", "广告", 2, 2)
    assert "张三" in msg and "广告" in msg


# ---------- store ----------
def test_store_warn_counter(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    assert st.bump_warn("g1", "张三", "广告") == 1
    assert st.bump_warn("g1", "张三", "刷单") == 2
    assert st.bump_warn("g1", "李四", "广告") == 1
    rows = st.warn_counts("g1")
    assert {r["member"]: r["n"] for r in rows} == {"张三": 2, "李四": 1}
    st.add_qa_pending("测试群", "王五", "NETLIST_FILE 怎么配", "")
    assert len(st.qa_pending()) == 1
    st.log("reply", "李明", "", "你好")
    assert st.recent_logs(5)[0]["kind"] == "reply"
