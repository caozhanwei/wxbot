# -*- coding: utf-8 -*-
"""人设引擎：按命名人生成 system 提示与回复/劝告话术。"""
import logging
import re

from llm import LLM, LLMError

log = logging.getLogger("wxbot.personas")

_COMMON_RULES = (
    "要求：只输出一条可直接发送的微信文本，不要解释、不要引号、不要markdown；"
    "像真人一样说话，别客服腔、别过度热情；涉及需要真实行程或要拍板的正经事，"
    "用简短含糊的话自然带过（如\"到时候看\"\"我说了算哈\"），不要编造具体细节。"
)

_WARN_RULES = "只输出一条发给群里的消息文本，20~60字，包含对{member}的提醒，不要解释。"


class PersonaEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            lc = self.cfg.llm_cfg
            self._llm = LLM(lc.get("provider", "custom"), lc.get("api_key", ""),
                            lc.get("model", ""), base_url=lc.get("base_url", ""),
                            timeout=int(lc.get("timeout", 60)))
        return self._llm

    def refresh(self):
        """配置热重载后重建 LLM 客户端。"""
        self._llm = None

    def system_for(self, persona_name, who):
        p = self.cfg.persona(persona_name)
        parts = [f'你在帮微信用户模仿他本人的口吻，自动回复"{who}"的消息。']
        if p.get("style"):
            parts.append(f"说话风格：{p['style']}。")
        if p.get("background"):
            parts.append(f"背景：{p['background']}。")
        parts.append(_COMMON_RULES)
        return "".join(parts)

    def chat_reply(self, persona_name, who, context_lines, last_text):
        system = self.system_for(persona_name, who)
        user = (f'最近的对话（"对方"是{who}，"我"是用户本人）：\n'
                + "\n".join(context_lines)
                + f'\n\n{who}最新消息：{last_text}\n请生成我的回复：')
        temp = float(self.cfg.llm_cfg.get("temperature", 0.8))
        raw = self.llm.chat(system, user, temperature=temp)
        return _sanitize(raw)

    def context_reply(self, persona_name, group, context_lines, kb_ref=""):
        """免@主动接话：没被@时按群聊上下文自然回复，可并入知识库参考。"""
        p = self.cfg.persona(persona_name)
        system = (f'你是微信群"{group}"里的助手。说话风格：{p.get("style")}。'
                  f'背景：{p.get("background") or "帮群友答疑的技术助手"}。'
                  "你没有被@，是看到群里有值得帮忙的话题顺带接一句。"
                  "只输出一条可直接发送的微信群文本：不要@任何人、不要解释、"
                  "不要引号、不要markdown，不超过 120 字；"
                  "没把握就直说不确定，别编造。")
        user = "群里最近的对话：\n" + "\n".join(context_lines)
        if kb_ref:
            user += ("\n\n知识库检索到的参考资料（可信时优先依据，与对话无关就忽略）：\n"
                     + kb_ref[:1200])
        user += "\n\n请生成你要发出的接话："
        temp = float(self.cfg.llm_cfg.get("temperature", 0.8))
        raw = self.llm.chat(system, user, temperature=temp)
        return _sanitize(raw)

    def warn_message(self, persona_name, member, word, nth, threshold):
        """违禁词劝告话术。"""
        p = self.cfg.persona(persona_name)
        system = (f"你是微信群的群管家。风格：{p.get('style')}。背景：{p.get('background') or '维护群秩序'}。"
                  + _WARN_RULES.format(member=member))
        escalate = "，并说明这是第%d次提醒、再有下次将请群主处理" % nth if nth >= threshold - 1 else ""
        user = (f'群成员"{member}"发了含违禁词"{word}"的内容（第 {nth} 次触发，'
                f'阈值 {threshold} 次后升级处理{escalate}）。请生成劝告消息：')
        try:
            raw = self.llm.chat(system, user, temperature=0.7)
            return _sanitize(raw) or f"@{member} 群内请勿发相关内容，注意群规哈"
        except Exception as e:
            log.warning("劝告话术生成失败，用兜底: %s", e)
            return f"@{member} 群内请勿发\"{word}\"相关内容，注意群规哈"


def _sanitize(text):
    t = (text or "").strip().strip('"\'「」').replace("\n", "  ")
    t = re.sub(r"\s{3,}", "  ", t)
    return t[:200]
