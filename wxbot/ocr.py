# -*- coding: utf-8 -*-
"""图片 OCR 客户端：走 paddle OCR MCP 服务（streamable-http）。

与 KB 网关同为 JSON-RPC over HTTP，但本服务要求会话：
initialize 的响应头 Mcp-Session-Id 必须在后续 tools/call 里回传。
响应是 SSE（单行 data:），且 requests 对 text/event-stream 会猜成
latin-1，必须显式 r.encoding='utf-8' 否则中文全乱。
"""
import base64
import json
import logging

import requests

log = logging.getLogger("wxbot.ocr")

_B64_DIRECT = 25000  # base64 长度上限，超过走 /upload + ocr_image


class OcrError(Exception):
    pass


class OcrClient:
    def __init__(self, cfg):
        cfg = cfg or {}
        self.url = (cfg.get("url") or "").rstrip("/")
        self.key = cfg.get("api_key") or ""
        self.timeout = int(cfg.get("timeout", 30))
        self._sid = None

    @property
    def ready(self):
        return bool(self.url and self.key)

    def _headers(self, rpc=True):
        h = {"x-api-key": self.key}
        if rpc:
            h.update({"Content-Type": "application/json",
                      "Accept": "application/json, text/event-stream"})
            if self._sid:
                h["Mcp-Session-Id"] = self._sid
        return h

    def _parse_sse(self, r):
        r.encoding = "utf-8"
        for line in r.text.splitlines():
            if line.startswith("data:"):
                d = json.loads(line[5:].strip())
                if d.get("error"):
                    raise OcrError(f"MCP 错误: {json.dumps(d['error'], ensure_ascii=False)[:120]}")
                return d
        raise OcrError(f"响应无 data 行: {r.text[:120]}")

    def _ensure_init(self):
        if self._sid:
            return
        r = requests.post(self.url + "/mcp", headers=self._headers(),
                          json={"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                "params": {"protocolVersion": "2024-11-05",
                                           "capabilities": {},
                                           "clientInfo": {"name": "wxbot", "version": "1.2"}}},
                          timeout=self.timeout)
        if r.status_code != 200:
            raise OcrError(f"initialize HTTP {r.status_code}: {r.text[:120]}")
        sid = r.headers.get("Mcp-Session-Id")
        if not sid:
            raise OcrError("initialize 未返回 Mcp-Session-Id")
        self._sid = sid

    def _call(self, name, arguments):
        """tools/call，会话过期自动重置重试一次。"""
        for attempt in range(2):
            self._ensure_init()
            r = requests.post(self.url + "/mcp", headers=self._headers(),
                              json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": name, "arguments": arguments}},
                              timeout=self.timeout)
            if r.status_code != 200 and "session" in r.text.lower():
                self._sid = None          # 会话失效，重initialize再来一轮
                if attempt == 0:
                    continue
            if r.status_code != 200:
                raise OcrError(f"HTTP {r.status_code}: {r.text[:120]}")
            d = self._parse_sse(r)
            result = d.get("result") or {}
            if result.get("isError"):
                raise OcrError(f"工具 {name} 返回错误")
            parts = result.get("content") or []
            inner_text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
            if not inner_text:
                return {}
            try:
                return json.loads(inner_text)
            except json.JSONDecodeError:
                return {"full_text": inner_text}

    def ocr(self, image_bytes):
        """返回识别出的文字（无文字/失败返回空串，调用方降级为 [图片]）。"""
        if not self.ready:
            return ""
        b64 = base64.b64encode(image_bytes).decode()
        if len(b64) <= _B64_DIRECT:
            inner = self._call("ocr_image_base64", {"image_base64": b64})
        else:
            r = requests.post(self.url + "/upload", headers=self._headers(rpc=False),
                              files={"file": ("img.jpg", image_bytes, "image/jpeg")},
                              timeout=self.timeout)
            if r.status_code != 200:
                raise OcrError(f"upload HTTP {r.status_code}: {r.text[:120]}")
            r.encoding = "utf-8"
            sp = (r.json() or {}).get("server_path")
            if not sp:
                raise OcrError("upload 未返回 server_path")
            inner = self._call("ocr_image", {"image_path": sp})
        return ((inner.get("full_text") or inner.get("text") or "") or "").strip()
