# -*- coding: utf-8 -*-
"""轻量 LLM 客户端（问答挖掘用）：任意 OpenAI 兼容网关，或智谱/百炼直连。"""
import json
import logging

import requests

log = logging.getLogger("llm")

_KNOWN = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "bailian": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, provider: str, api_key: str, model: str,
                 base_url: str = "", timeout: int = 90):
        if base_url:
            self.base = base_url.rstrip("/")
        elif provider in _KNOWN:
            self.base = _KNOWN[provider]
        else:
            raise LLMError(f"未知 LLM provider: {provider}（或直接配 base_url）")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def chat(self, system: str, user: str, temperature: float = 0.1,
             max_tokens: int = 2000) -> str:
        if not self.ready:
            raise LLMError("LLM api_key 未配置（config.yaml llm 段）")
        # 推理型模型（reasoning_content）会先消耗预算，max_tokens 太小 content 会被吃空
        content = ""
        for attempt in range(2):
            resp = requests.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                }, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise LLMError(f"LLM 响应格式异常: {e}: {json.dumps(data, ensure_ascii=False)[:300]}")
            if (content or "").strip():
                return content.strip()
            log.warning("LLM content 为空（可能被 reasoning 占满），重试 %d/1", attempt + 1)
        raise LLMError("LLM content 为空（重试后仍为空）")

    def chat_json(self, system: str, user: str, temperature: float = 0.1):
        """要求模型输出 JSON 并解析。"""
        text = self.chat(system + "\n只输出 JSON，不要输出任何其他文字、注释或 markdown 代码块标记。", user, temperature)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        return json.loads(text)
