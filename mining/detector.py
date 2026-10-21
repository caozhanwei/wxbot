# -*- coding: utf-8 -*-
"""问题识别：纯启发式（零成本、毫秒级），不打 LLM。

设计取舍：群消息量大，每条都过 LLM 既慢又费钱；启发式召回足够做候选，
最终问答对还要经过管理员审核，误报的代价可控。
"""
import re

_QUESTION_START = re.compile(
    r"^(请问|咨询下|问一下|求助|请教|怎么|如何|为啥|为什么|为何|哪里|哪儿|哪个|有没有|是不是|能不能|可不可以|能不能|可否|啥|什么)")
_QUESTION_END = re.compile(
    r"(吗[?？。!！~]?$|呢[?？。!！~]?$|怎么[办搞弄处理]?$|如何[处理设置配置解决]?$|是什么|是啥|在哪|哪里找)")

# 短命令式消息（如 "NETLIST_FILE" / "a01.table"）不算问题
_MIN_LEN = 6


def is_question(text: str) -> bool:
    text = (text or "").strip()
    if not text or len(text) < _MIN_LEN:
        return False
    if _QUESTION_START.search(text):
        return True
    if _QUESTION_END.search(text):
        return True
    if "?" in text or "？" in text:
        # 含问号且带一定上下文长度，视为提问
        return len(text) >= _MIN_LEN + 2
    return False
