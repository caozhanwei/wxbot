# -*- coding: utf-8 -*-
"""画像/问答库浏览：按群过滤 + 分页（store 层与 webui 层，TDD 先行）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import profiles as profiles_mod
from wxbot.store import Store

from test_webui import client  # noqa: F401  复用 StubBot + test_client 夹具

_PAGE = 20  # 与 webui 页大小一致


# ---------- store 层 ----------

def _seed_profiles(store):
    for i in range(25):
        store.upsert_profile("111@chatroom", f"u{i:02d}", f"成员{i:02d}", f"画像{i:02d}")
    for i in range(2):
        store.upsert_profile("9@chatroom", f"v{i}", f"邻居{i}", f"画像v{i}")


def test_store_profiles_count_and_paging(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    _seed_profiles(store)
    assert store.profiles_count() == 27
    assert store.profiles_count("111@chatroom") == 25
    p1 = store.profiles("111@chatroom", limit=_PAGE, offset=0)
    p2 = store.profiles("111@chatroom", limit=_PAGE, offset=_PAGE)
    assert len(p1) == _PAGE and len(p2) == 5
    ids1 = {r["wxid"] for r in p1}
    assert ids1 | {r["wxid"] for r in p2} == {f"u{i:02d}" for i in range(25)}
    assert not ids1 & {r["wxid"] for r in p2}


def test_store_mined_count_and_paging(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.add_mined("111@chatroom", [(f"问{i}", f"答{i}") for i in range(25)])
    store.add_mined("9@chatroom", [("问z", "答z")])
    assert store.mined_count() == 26 and store.mined_count("111@chatroom") == 25
    p2 = store.mined_pairs("111@chatroom", limit=_PAGE, offset=_PAGE)
    assert len(p2) == 5 and all(r["grp"] == "111@chatroom" for r in p2)


def test_profile_length_cap_1000():
    assert "1000" in profiles_mod._SYSTEM     # 提示词放宽到 1000 字


def test_upsert_profile_keeps_1000_chars(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.upsert_profile("g", "u", "n", "细" * 1000)
    assert len(store.get_profile("g", "u")["profile"]) == 1000


# ---------- webui 层 ----------

def test_profiles_group_filter_and_paging(client):
    c, bot, _ = client
    _seed_profiles(bot.store)
    body = c.get("/").data.decode("utf-8")
    assert "第 1 / 2 页" in body and "下一页" in body          # 全部 27 条 → 2 页
    assert "测试群" in body                                     # 群过滤链接
    body2 = c.get("/?p_grp=111@chatroom&p_page=2").data.decode("utf-8")
    assert "第 2 / 2 页" in body2 and "上一页" in body2
    body3 = c.get("/?p_grp=9@chatroom").data.decode("utf-8")
    assert "第 1 / 1 页" in body3 and "邻居0" in body3 and "成员00" not in body3


def test_profiles_filter_keeps_mined_filter(client):
    c, bot, _ = client
    _seed_profiles(bot.store)
    bot.store.add_mined("111@chatroom", [("问1", "答1")])
    bot.store.add_mined("9@chatroom", [("问z", "答z")])
    body = c.get("/?p_grp=9@chatroom").data.decode("utf-8")
    # 画像切到别的群，问答过滤应保持默认（全部），两个群的问答都在
    assert "问1" in body and "问z" in body


def test_mined_group_filter_and_paging(client):
    c, bot, _ = client
    bot.store.add_mined("111@chatroom", [(f"问{i:02d}", f"答{i:02d}") for i in range(25)])
    bot.store.add_mined("9@chatroom", [("问z", "答z")])
    body = c.get("/?m_grp=111@chatroom&m_page=2").data.decode("utf-8")
    assert "第 2 / 2 页" in body and "上一页" in body and "问z" not in body
    body2 = c.get("/?m_grp=9@chatroom").data.decode("utf-8")
    assert "第 1 / 1 页" in body2 and "问z" in body2 and "问00" not in body2
    # 问答行里的群显示为配置里的群名（按群区分可读）
    assert "测试群" in body2
