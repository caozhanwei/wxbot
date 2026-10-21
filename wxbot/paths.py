# -*- coding: utf-8 -*-
"""路径解析：开发态用当前目录；打包后（frozen）数据落 %APPDATA%\\wxbot。

多账号隔离：登录后驱动会上报自身 wxid，maybe_switch_account() 把配置/数据库
切换到 %APPDATA%\\wxbot\\<wxid>\\ 子目录——换微信号登录自动换一套配置与数据，
互不串号；同一账号每次登录都用回自己的目录。
"""
import logging
import os
import re
import shutil
import sys

import yaml

log = logging.getLogger("wxbot.paths")

_account = None  # 当前账号子目录名（仅 frozen 态生效）


def is_frozen():
    return getattr(sys, "frozen", False)


def _base_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "wxbot")


def _safe_name(wxid):
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(wxid))


def set_account(wxid):
    """登录后设定当前账号；此后 resolve() 落到账号子目录。"""
    global _account
    if not is_frozen() or not wxid:
        return
    _account = _safe_name(wxid)


def data_dir():
    if is_frozen():
        d = _base_dir()
        if _account:
            d = os.path.join(d, _account)
    else:
        d = os.getcwd()
    os.makedirs(d, exist_ok=True)
    return d


def resolve(name):
    """相对路径映射到数据目录（数据库/日志等运行期产物）。"""
    if os.path.isabs(name):
        return name
    return os.path.join(data_dir(), name)


def template_path():
    """内置配置模板：打包后在 _MEIPASS，源码态在仓库根。"""
    return os.path.join(
        getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "bot_config_template.yaml")


def default_config():
    """默认配置文件路径（根目录）；打包后首次运行时从内置模板生成。

    注意：根目录这份只是"登录前/首启"用；登录成功后由
    maybe_switch_account() 切到 %APPDATA%\\wxbot\\<wxid>\\bot_config.yaml。
    """
    if not is_frozen():
        return "bot_config.yaml"
    target = os.path.join(_base_dir(), "bot_config.yaml")
    if not os.path.exists(target):
        tpl = template_path()
        if os.path.exists(tpl):
            shutil.copy(tpl, target)
        else:
            raise FileNotFoundError("缺少内置配置模板 bot_config_template.yaml")
    return target


def maybe_switch_account(cfg_path, self_id):
    """按登录 wxid 隔离配置/数据库，返回应使用的配置路径。

    - 开发态（非 frozen）原样返回，不隔离；
    - 本账号已有目录 → 直接用；
    - 本账号首次使用：根配置尚未绑定过其他账号 → 继承（老用户升级/换机无损），
      已绑定别的账号 → 从内置模板开一套新配置（新微信号干净起步）。
    """
    if not is_frozen() or not self_id:
        return cfg_path
    root = _base_dir()
    acct = os.path.join(root, _safe_name(self_id))
    target = os.path.join(acct, "bot_config.yaml")
    os.makedirs(acct, exist_ok=True)
    if os.path.abspath(cfg_path) == os.path.abspath(target):
        set_account(self_id)
        _mark_self(target, self_id)
        return target
    if not os.path.exists(target):
        cur = {}
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cur = yaml.safe_load(f) or {}
        except OSError:
            cur = {}
        prev = str(((cur.get("app") or {}).get("self_wxid") or "")).strip()
        if prev and prev != self_id:
            # 这台机器之前登录过别的账号：新账号从模板起步，不带旧联系人
            shutil.copyfile(template_path(), target)
            log.info("账号 %s 首次使用，已从模板初始化新配置", self_id)
        else:
            if os.path.exists(cfg_path):
                shutil.copyfile(cfg_path, target)
            db_file = str((cur.get("app") or {}).get("db_file") or "wxbot.db")
            if not os.path.isabs(db_file):
                src_db = os.path.join(root, db_file)
                if os.path.exists(src_db) and not os.path.exists(os.path.join(acct, db_file)):
                    try:
                        shutil.copyfile(src_db, os.path.join(acct, db_file))
                    except OSError as e:
                        log.warning("数据库迁移失败（沿用根目录旧库）: %s", e)
            _mark_self(cfg_path, self_id)  # 根配置标记归属，后续其他账号不再继承它
            log.info("账号 %s 配置/数据库已迁入 %s", self_id, acct)
    set_account(self_id)
    _mark_self(target, self_id)
    return target


def _mark_self(cfg_path, self_id):
    """把 self_wxid 写进配置（幂等；仅当变化时重写，避免无谓落盘）。"""
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if str((data.get("app") or {}).get("self_wxid") or "") == str(self_id):
            return
        data.setdefault("app", {})["self_wxid"] = self_id
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    except OSError as e:
        log.warning("写入 self_wxid 失败: %s", e)
