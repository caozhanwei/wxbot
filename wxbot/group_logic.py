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


_LEAVE_PATTERN = re.compile(r"\"(.+?)\"(?:退出了群聊|被\".+?\"移出了群聊)")


def leave_name(content):
    """系统消息中退群/被移出者的昵称，非退群类消息返回 None。"""
    m = _LEAVE_PATTERN.search(content or "")
    return m.group(1) if m else None


# 欢迎语占位符的各种民间写法都认（真出过事：配置里写 $names$ 原样发了出去）
_WELCOME_PLACEHOLDERS = ("{name}", "{昵称}", "{nickname}", "{新人}",
                         "$name$", "$names$", "$昵称$", "%name%")


def render_welcome(text, name):
    """欢迎语占位符容错替换：{name}/$name$/$names$/%name%/{昵称} 等一律替换。

    刻意不用 str.format——用户文本里再有别的花括号（如 {@我}）不会炸，
    不认识的占位符原样保留。"""
    for ph in _WELCOME_PLACEHOLDERS:
        if ph in text:
            text = text.replace(ph, name)
    return text


# 用户吐槽机器人的常见用词（收集改进线索，宁多勿漏——每日 LLM 复盘时再过滤噪音）
COMPLAINT_WORDS = (
    "蠢", "傻", "答非所问", "答错", "回错", "答不上", "没答", "不回答",
    "胡说", "胡答", "瞎答", "瞎回", "乱答", "乱回", "不准", "不对",
    "没用", "废物", "关掉", "别回", "别答", "烦人", "真烦", "离谱",
    "拉胯", "人工智障", "智障", "垃圾", "不如不用", "浪费时间",
    "什么破", "看不懂", "答的什么", "回的什么", "越答越",
    "牛头不对马嘴", "驴唇不对马嘴", "文不对题", "不知所云",
)
_BOT_MENTION_RE = re.compile(r"机器人|群助手|助手|AI|人工智能|智能中心|智能客服")


def complaint_hits(content, bot_names=(), quoted_bot=False, private=False):
    """内容里对机器人的吐槽词命中；返回命中词列表（空=不算吐槽）。

    群聊（private=False）要求同时"提到机器人"（称呼命中/引用机器人消息/
    通用称呼词）才收，防止把吐槽别人的也捞进来；私聊/对话台是 1:1 对着
    机器人说话，命中吐槽词即算。"""
    text = content or ""
    hits = [w for w in COMPLAINT_WORDS if w in text]
    if not hits:
        return []
    if private:
        return hits
    if quoted_bot or _BOT_MENTION_RE.search(text):
        return hits
    for n in bot_names or ():
        if n and n in text:
            return hits
    return []


# 广告号常见话术特征：新成员进群即发这些，基本可断定营销号
ADS_DEFAULT_KEYWORDS = (
    "加微信", "加我vx", "加V信", "薇信", "兼职", "刷单", "返利", "优惠券",
    "低价出", "秒到账", "日赚", "招代理", "招兼职", "办证", "开发票", "贷款",
    "无抵押", "私聊我", "扫码进群", "免费领", "点击链接", "内部渠道",
)
_ADS_LINK = re.compile(r"https?://|www\.", re.I)


def ads_markers(content, extra=()):
    """文本命中的广告特征清单（去重）：链接记 1 个 + 命中的关键词。

    告警阈值由调用方定（≥2 个特征才告警，单个词只可能是正常聊天）。"""
    text = (content or "").strip()
    if not text:
        return []
    marks = []
    if _ADS_LINK.search(text):
        marks.append("链接")
    for kw in tuple(ADS_DEFAULT_KEYWORDS) + tuple(extra or ()):
        kw = (kw or "").strip()
        if kw and kw in text and kw not in marks:
            marks.append(kw)
    return marks


def forbidden_hit(content, words):
    """返回命中的第一个违禁词，未命中返回 None。"""
    for w in words or []:
        if w and w in content:
            return w
    return None
