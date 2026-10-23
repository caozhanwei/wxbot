# -*- coding: utf-8 -*-
"""群消息纯逻辑（无 IO，可单测）：解析、@检测、违禁词、进群识别。"""
import re

# 微信群消息 content 形如 "wxid_xxx: 正文"（4.x 本地库把发送者嵌在正文前）
_GROUP_PREFIX = re.compile(r"^([A-Za-z][\w-]+):\s?", re.UNICODE)

_JOIN_PATTERNS = [
    re.compile(r"邀请\"(.+?)\"加入了?群聊"),
    re.compile(r"\"(.+?)\"通过扫描.*?二维码.*?加入了?群聊"),
    re.compile(r"\"(.+?)\"加入了?群聊"),
]


def parse_group_content(content):
    """拆出 (sender_id, 正文)；无前缀返回 (None, content)。"""
    m = _GROUP_PREFIX.match(content or "")
    if m:
        return m.group(1), content[m.end():]
    return None, content or ""


def at_bot(content, names):
    """正文里是否 @了机器人（匹配任一称呼，@后跟空格/\\u2005/结尾）。"""
    for name in names:
        if not name:
            continue
        for m in re.finditer(re.escape("@" + name), content):
            tail = content[m.end():m.end() + 1]
            if tail in ("", " ", " ", "\n", ":"):
                return True
    return False


def strip_at(text, names):
    """去掉 @机器人 的部分，返回剩余问题文本。"""
    for name in names:
        if not name:
            continue
        text = re.sub(re.escape("@" + name) + r"[\s :]*", "", text)
    return text.strip()


_ATUSERLIST = re.compile(r"<atuserlist><!\[CDATA\[(.*?)\]\]></atuserlist>")


def parse_atuserlist(source):
    """从 msgsource XML 提取被 @ 的 wxid 列表（微信写入的精确名单）。

    群成员 @ 机器人时正文用的是群名片名，与微信昵称（mention_names）可能
    不同，文本匹配会漏；atuserlist 直接给出被 @ 者的 wxid，无歧义。
    """
    if not source:
        return []
    if isinstance(source, bytes):
        try:
            source = source.decode("utf-8", "ignore")
        except Exception:
            return []
    m = _ATUSERLIST.search(source)
    if not m:
        return []
    return [u for u in m.group(1).split(",") if u]


_GENERIC_AT = re.compile(r"^@[^\s :]{1,32}[\s :]*")


def strip_generic_at(text):
    """剥掉开头第一处 @提及 前缀（称呼未知时用；仅在确认被 @ 时调用）。"""
    return _GENERIC_AT.sub("", text or "", count=1).strip()


def join_name(content):
    """系统消息中的进群者昵称，非进群消息返回 None。"""
    for pat in _JOIN_PATTERNS:
        m = pat.search(content or "")
        if m:
            return m.group(1)
    return None


def forbidden_hit(content, words):
    """返回命中的第一个违禁词，未命中返回 None。"""
    for w in words or []:
        if w and w in content:
            return w
    return None
