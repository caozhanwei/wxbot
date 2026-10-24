# -*- coding: utf-8 -*-
"""知识库答疑客户端（MCP 协议，流式 HTTP）。

后端是团队网关 zhipu-gateway-kb（https://pv-tokenfactory.eda2.com/mcp）：
  tools/call kb_ask(query, kb|kb_id, top_k)  RAG 问答，答案带"参考来源"
  tools/call kb_search(...)                  纯检索（备用）
"""
import json
import logging

import requests

log = logging.getLogger("kb")

_INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "wechat-assistant", "version": "1.0"}}}


class KBError(Exception):
    pass


class McpKBClient:
    def __init__(self, config, db=None):
        mcp = config.get("mcp") or {}
        self.url = mcp.get("url", "")
        self.key = mcp.get("api_key", "")
        self.kb = mcp.get("kb", "team")
        self.top_k = int(mcp.get("top_k", 5))
        self.timeout = int(mcp.get("timeout", 60))
        # 应答检索快速失败秒数：答疑等不了 RAG 慢响应，超时走兜底；
        # 写入类（add_qa/upload）仍用长超时（多为后台线程，慢点无妨）
        self.ask_timeout = int(mcp.get("ask_timeout", 8))
        self.miss_markers = mcp.get("miss_markers") or [
            "未查询到", "无法回答", "没有找到", "未找到相关", "未提及", "无相关内容", "未提供"]
        self._initialized = False

    @property
    def ready(self) -> bool:
        return bool(self.url and self.key)

    def _rpc(self, method, params=None, id_=1, timeout=None) -> dict:
        payload = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            payload["params"] = params
        r = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"},
            json=payload, timeout=timeout or self.timeout)
        if r.status_code != 200:
            raise KBError(f"HTTP {r.status_code}: {r.text[:200]}")
        for line in r.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                d = json.loads(line)
                if "error" in d and d["error"]:
                    raise KBError(f"MCP 错误: {json.dumps(d['error'], ensure_ascii=False)[:200]}")
                return d
        raise KBError(f"响应中无 JSON: {r.text[:200]}")

    def _ensure_init(self):
        if not self._initialized:
            self._rpc("initialize", _INIT["params"], id_=0)
            self._initialized = True

    def _call_tool(self, name, arguments, timeout=None):
        self._ensure_init()
        d = self._rpc("tools/call", {"name": name, "arguments": arguments},
                      timeout=timeout)
        result = d.get("result", {})
        parts = result.get("content") or []
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
        if result.get("isError"):
            raise KBError(f"工具 {name} 返回错误: {text[:200]}")
        return text

    def ask(self, question: str, room: str = ""):
        """RAG 问答。返回 (answer, hit, grounded)：
        hit=正中；grounded=带引用来源（即使含"未提及"等字样，答案仍有参考价值）。"""
        text = self._call_tool("kb_ask", {"query": question, "kb": self.kb, "top_k": self.top_k},
                               timeout=self.ask_timeout)
        if not text:
            raise KBError("kb_ask 返回空")
        hit = not any(m in text for m in self.miss_markers)
        grounded = ("来源" in text or "[1]" in text) and len(text) > 60
        return text, hit, grounded

    def search(self, query: str, top_k: int = 5) -> str:
        return self._call_tool("kb_search", {"query": query, "kb": self.kb, "top_k": top_k},
                               timeout=self.ask_timeout)
