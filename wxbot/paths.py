# -*- coding: utf-8 -*-
"""路径解析：开发态用当前目录；打包后（frozen）数据落 %APPDATA%\\wxbot。"""
import os
import shutil
import sys


def is_frozen():
    return getattr(sys, "frozen", False)


def data_dir():
    if is_frozen():
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "wxbot")
    else:
        d = os.getcwd()
    os.makedirs(d, exist_ok=True)
    return d


def resolve(name):
    """相对路径映射到数据目录（数据库/日志等运行期产物）。"""
    if os.path.isabs(name):
        return name
    return os.path.join(data_dir(), name)


def default_config():
    """默认配置文件路径；打包后首次运行时从内置模板生成。"""
    if not is_frozen():
        return "bot_config.yaml"
    target = os.path.join(data_dir(), "bot_config.yaml")
    if not os.path.exists(target):
        tpl = os.path.join(getattr(sys, "_MEIPASS", "."), "bot_config_template.yaml")
        if os.path.exists(tpl):
            shutil.copy(tpl, target)
        else:
            raise FileNotFoundError("缺少内置配置模板 bot_config_template.yaml")
    return target
