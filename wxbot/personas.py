# -*- coding: utf-8 -*-
"""人设引擎：按命名人生成 system 提示与回复/劝告话术。"""
import logging
import random
import re

from llm import LLM, LLMError

log = logging.getLogger("wxbot.personas")

_COMMON_RULES = (
    "要求：只输出一条可直接发送的微信文本，不要解释、不要引号、不要markdown；"
    "像真人一样说话，别客服腔、别过度热情；涉及需要真实行程或要拍板的正经事，"
    "用简短含糊的话自然带过（如\"到时候看\"\"我说了算哈\"），不要编造具体细节。"
)

_WARN_RULES = "只输出一条发给群里的消息文本，20~60字，包含对{member}的提醒，不要解释。"


def split_reply_bubbles(text: str) -> list[str]:
    """将一条回复拆分为 1~3 个符合微信真人聊天习惯的短气泡。

    长句按自然标点（句号/感叹号/分号/换行）切分，过短（≤15字）不强拆。
    """
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= 18:
        return [t]

    # 按自然标点分割
    parts = re.split(r"([。！？\n!?;；]+)", t)
    sentences = []
    curr = ""
    for p in parts:
        if not p:
            continue
        curr += p
        if re.search(r"[。！？\n!?;；]$", p):
            s = curr.strip()
            if s:
                sentences.append(s)
            curr = ""
    if curr.strip():
        sentences.append(curr.strip())

    if len(sentences) <= 1:
        return [t]
    if len(sentences) == 2:
        return sentences
    # 若过多，合并成 2~3 组
    if len(sentences) >= 3:
        b1 = sentences[0]
        b2 = sentences[1]
        b3 = "".join(sentences[2:])
        return [b1, b2, b3] if len(b3) > 0 else [b1, b2]
    return sentences


def human_delay_for_text(text: str) -> float:
    """计算拟真的手机微信打字与阅读延时（秒）。

    基础思考 1.0~2.0s + 字符打字耗时（约0.08~0.15s/字），有随机抖动，上限12秒。
    """
    n = len(text or "")
    base = 1.0 + random.uniform(0.2, 0.8)
    type_time = n * 0.11 + random.uniform(-0.1, 0.3)
    total = base + max(0.0, type_time)
    return min(12.0, max(1.0, round(total, 2)))


def clean_ai_markers(text: str) -> str:
    """彻底清除 AI 答题机口头禅、客服公文腔以及句尾多余句号。"""
    t = (text or "").strip()
    # 清理开头客套寒暄
    t = re.sub(r"^(您好[！!，,\s]*|你好[！!，,\s]*|关于您[咨询提问]+的[问题事宜]*[：:，,\s]*|很高兴为您解答[：:，,\s]*)", "", t)
    # 清理结尾死客套
    t = re.sub(r"(希望对您有所帮助[。！!]*|如有其他问题随时[问联系]我[。！!]*|祝您生活愉快[。！!]*)$", "", t)
    # 替换公文分点
    t = re.sub(r"首先[，,]*\s*", "", t)
    t = re.sub(r"其次[，,]*\s*", "另外，", t)
    # 句末剥离。
    t = re.sub(r"[。]+$", "", t.strip())
    return t.strip()


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

    @llm.setter
    def llm(self, value):
        self._llm = value

    def refresh(self):
        """配置热重载后重建 LLM 客户端。"""
        self._llm = None

    def system_for(self, persona_name, who, memory=""):
        p = self.cfg.persona(persona_name)
        parts = [f'你在帮微信用户模仿他本人的口吻，自动回复"{who}"的消息。']
        if p.get("style"):
            parts.append(f"说话风格：{p['style']}。")
        if p.get("background"):
            parts.append(f"背景：{p['background']}。")
        if memory:
            parts.append(f"你对这位好友的长期生活记忆（重要事实、家人、爱好等）：{memory}。在对话中自然融入，切勿机械重复。")
        parts.append(_COMMON_RULES)
        return "".join(parts)

    def chat_reply(self, persona_name, who, context_lines, last_text, memory=""):
        system = self.system_for(persona_name, who, memory=memory)
        user = (f'最近的对话（"对方"是{who}，"我"是用户本人）：\n'
                + "\n".join(context_lines)
                + f'\n\n{who}最新消息：{last_text}\n请生成我的回复：')
        temp = float(self.cfg.llm_cfg.get("temperature", 0.8))
        raw = self.llm.chat(system, user, temperature=temp)
        return clean_ai_markers(_sanitize(raw))

    def extract_contact_memory(self, who, dialog_lines, current_memory=""):
        """从私聊对话中提炼/更新对该联系人的关键长期记忆点（家庭、喜好、工作、约定）。"""
        sys = (
            "你是微信私人助手。根据与好友的对话，提炼出关于该好友的重要个人事实与长期记忆"
            "（如家庭成员、车型/买车、兴趣爱好、常居地、重要项目或约定事项）。"
            "要求：输出不超过 100 字的关键事实摘要；若无新增价值事实，返回原有记忆。"
        )
        usr = f"好友：{who}\n原有记忆：{current_memory or '无'}\n近期对话：\n" + "\n".join(dialog_lines[-10:])
        try:
            raw = self.llm.chat(sys, usr, temperature=0.1)
            return _sanitize(raw)[:200]
        except Exception as e:
            log.warning("提炼联系人记忆失败: %s", e)
            return current_memory

    def context_reply(self, persona_name, group, context_lines, kb_ref="", active=False):
        """免@主动接话：没被@时按群聊上下文自然回复，可并入知识库参考。

        active=True（活跃分子模式）：接话姿态更放松，接梗/附议/补充/引申提问都行，
        像真人群友一样自然参与，不做"只在值得帮忙时才说话"的束缚。
        """
        p = self.cfg.persona(persona_name)
        if active:
            tone = ("你是这个群里的活跃分子，习惯每个话题都冒泡：接梗、附议、补充信息、"
                    "引申提问都行，像真人群友一样自然参与。")
        else:
            tone = "你没有被@，是看到群里有值得帮忙的话题顺带接一句。"
        system = (f'你是微信群"{group}"里的助手。说话风格：{p.get("style")}。'
                  f'背景：{p.get("background") or "帮群友答疑的技术助手"}。' + tone +
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

    def understand_question(self, group, context_lines, question, asker, profile=""):
        """结合群聊上下文理解@提问：补全指代成独立问题 + 判断是否真在求答。

        返回 {"standalone": str, "should_answer": bool}；失败抛异常由调用方降级。
        """
        system = (
            "你是微信群助手的提问理解器。结合群里最近的对话，把提问者的问题改写成一个"
            "不依赖上下文也能看懂的独立问题（补全指代，如\"它多少钱\"要落到具体在问什么），"
            "并判断他是否真的在求助/求答案。"
            '只输出 JSON：{"standalone": "改写后的独立问题", "should_answer": true或false}。'
            "should_answer=false：闲聊寒暄、反问、自问自答、开玩笑、纯@着玩；"
            "true：明确提出问题、求助、求资源/文档。改写保持原意，别添造信息。"
        )
        user = f"群：{group}\n提问者：{asker}"
        if profile:
            user += f"（画像：{profile}）"
        user += ("\n最近对话：\n" + "\n".join(context_lines[-12:])
                 + f"\n\n提问：{question}\n分析：")
        d = self.llm.chat_json(system, user, temperature=0.0)
        standalone = str(d.get("standalone") or "").strip() or question
        return {"standalone": standalone, "should_answer": bool(d.get("should_answer", True))}

    def group_answer(self, persona_name, group, context_lines, question,
                     asker, profile="", kb_ref="", self_profile=""):
        """@提问的群内回答：结合上下文与双方画像，像真人接话一样答，可引用群友观点。"""
        p = self.cfg.persona(persona_name)
        system = (f'你是微信群"{group}"里的助手（有人@你提问）。'
                  f"回答风格：{p.get('style')}。背景：{p.get('background') or '帮群友答疑'}。"
                  "结合群里最近的讨论来回答：像顺着大家话头接话，不重复别人已说透的点，"
                  "可以自然引用群友昵称提到的观点（如\"刚才xx说的对\"）；"
                  "答案不确定就直说，别编造。"
                  "只输出一条可直接发送的微信群文本：不要@提问者以外的其他人、"
                  "不要解释、不要引号、不要markdown，不超过 200 字。")
        user = "群里最近的对话：\n" + "\n".join(context_lines)
        if self_profile:
            user += f"\n\n你本人（号主）在群里的画像，回答口吻与立场保持一致：{self_profile}"
        if profile:
            user += f"\n\n提问者画像：{profile}"
        user += f"\n\n{asker} 的问题：{question}"
        if kb_ref:
            user += ("\n\n知识库检索到的参考资料（可信时优先依据，与问题无关就忽略）：\n"
                     + kb_ref[:1200])
        user += "\n\n请生成你的回答："
        temp = float(self.cfg.llm_cfg.get("temperature", 0.8))
        raw = self.llm.chat(system, user, temperature=temp)
        return _sanitize(raw)

    def casual_group_reply(self, persona_name, group, context_lines, asker, question, profile="", self_profile=""):
        """群聊闲聊、调侃、玩笑或纯@时的拟人回复：拒绝死板机器人话术，幽默机智接地气。"""
        p = self.cfg.persona(persona_name)
        system = (
            f'你是微信群"{group}"里的群友兼小助手。'
            f"你的说话风格：{p.get('style') or '幽默风趣、接地气、机智嘴甜'}。"
            f"背景：{p.get('background') or '群里的常驻活跃分子'}。"
            "群友现在在跟你开玩笑、打趣调侃、闲聊或者纯@你互动，并不是正经的技术/业务提问。"
            "请像个熟人朋友一样自然接话或打趣，语气生动接地气，可以带适当微信自带表情符号（如 [呲牙] [捂脸] [笑哭]）。"
            "绝对禁止说'哈哈我在呢'、'有问题随时问我'、'我是人工智能'等任何机器人客服死模板！"
            "只输出一条直接可发的微信短句，不要超过 50 字，不要带任何前缀解释。"
        )
        user = f"群：{group}\n对方：{asker}"
        if profile:
            user += f"（群友特点：{profile}）"
        if self_profile:
            user += f"\n你本人的画像（保持一致的说话习惯）：{self_profile}"
        user += ("\n最近群聊对话：\n" + "\n".join(context_lines[-8:])
                 + f"\n\n对方说：{question}\n你的接话：")
        try:
            temp = float(self.cfg.llm_cfg.get("temperature", 0.85))
            raw = self.llm.chat(system, user, temperature=temp)
            reply = clean_ai_markers(_sanitize(raw))
            return reply or f"在呢在呢，刚忙完一眼就看到大佬召唤 [呲牙]"
        except Exception as e:
            log.warning("生成群闲聊接话失败: %s", e)
            return f"哈哈收到，刚才正潜水呢 [坏笑]"

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
