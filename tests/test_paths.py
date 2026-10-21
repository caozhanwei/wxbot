# -*- coding: utf-8 -*-
"""多账号隔离：maybe_switch_account 的继承/复用/新账号模板起步（模拟 frozen 态）。"""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxbot import paths

CFG = """
app: {dry_run: true, db_file: wxbot.db}
llm: {base_url: "http://x/v1", api_key: k, model: m}
contacts:
  - {id: wxid_1, display: 张三, enabled: true, persona: 通用朋友}
"""


@pytest.fixture
def frozen_env(monkeypatch, tmp_path):
    """假装打包运行：APPDATA 指向临时目录，并重置账号全局。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(paths, "_account", None)
    root = paths._base_dir()
    os.makedirs(root, exist_ok=True)
    return root


def test_dev_mode_no_switch(tmp_path):
    cfg = str(tmp_path / "bot_config.yaml")
    assert paths.maybe_switch_account(cfg, "wxid_x") == cfg


def test_first_account_inherits(frozen_env, tmp_path):
    root = frozen_env
    cfg = os.path.join(root, "bot_config.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(CFG)
    open(os.path.join(root, "wxbot.db"), "w").close()
    new = paths.maybe_switch_account(cfg, "wxid_aaa")
    assert os.path.abspath(new) == os.path.join(root, "wxid_aaa", "bot_config.yaml")
    # 数据目录切到账号子目录，数据库跟着迁
    assert paths.data_dir() == os.path.join(root, "wxid_aaa")
    assert os.path.exists(os.path.join(root, "wxid_aaa", "wxbot.db"))
    assert paths.resolve("x.db") == os.path.join(root, "wxid_aaa", "x.db")
    with open(new, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["app"]["self_wxid"] == "wxid_aaa"
    assert data["contacts"][0]["display"] == "张三"          # 老配置无损继承


def test_same_account_relogin_uses_same_dir(frozen_env, tmp_path):
    root = frozen_env
    cfg = os.path.join(root, "bot_config.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(CFG)
    a = paths.maybe_switch_account(cfg, "wxid_aaa")
    a2 = paths.maybe_switch_account(cfg, "wxid_aaa")          # 已在账号目录 → 直接用
    assert os.path.abspath(a2) == os.path.abspath(a)


def test_second_account_starts_from_template(frozen_env, tmp_path):
    root = frozen_env
    cfg = os.path.join(root, "bot_config.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(CFG)
    paths.maybe_switch_account(cfg, "wxid_aaa")
    b = paths.maybe_switch_account(cfg, "wxid_bbb")           # 换微信号 → 模板新配置
    with open(b, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["app"]["self_wxid"] == "wxid_bbb"
    assert "张三" not in str(data.get("contacts"))             # 不带 A 号的联系人
    assert data["app"]["dry_run"] is True                      # 模板默认演示模式
    assert data["app"]["setup_done"] is False                  # 走首启向导
    assert paths.data_dir() == os.path.join(root, "wxid_bbb")
