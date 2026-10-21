# -*- coding: utf-8 -*-
"""MCP 知识库客户端：SSE/JSON 响应解析、错误处理、命中判定。"""
import json

import pytest

from answer import McpKBClient, KBError


def _client():
    return McpKBClient({"mcp": {"url": "https://kb.test/mcp", "api_key": "k",
                                "kb": "team", "top_k": 5, "timeout": 5}})


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


def test_ready():
    c = _client()
    assert c.ready
    assert not McpKBClient({"mcp": {}}).ready


def test_rpc_plain_json(monkeypatch):
    import answer
    monkeypatch.setattr(answer.requests, "post",
                        lambda *a, **kw: _FakeResp('{"jsonrpc":"2.0","id":1,"result":{"ok":1}}'))
    assert _client()._rpc("tools/list") == {"jsonrpc": "2.0", "id": 1, "result": {"ok": 1}}


def test_rpc_sse_data(monkeypatch):
    import answer
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"答案A"}]}}\n\n'
    monkeypatch.setattr(answer.requests, "post", lambda *a, **kw: _FakeResp(sse))
    text = _client()._call_tool("kb_ask", {"query": "q"})
    assert text == "答案A"


def test_rpc_error(monkeypatch):
    import answer
    monkeypatch.setattr(answer.requests, "post",
                        lambda *a, **kw: _FakeResp('{"jsonrpc":"2.0","id":1,"error":{"message":"bad"}}'))
    with pytest.raises(KBError):
        _client()._rpc("tools/list")


def test_ask_hit_and_miss(monkeypatch):
    import answer

    def resp_with(text):
        return _FakeResp('data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":%s}]}}'
                         % json.dumps(text))

    monkeypatch.setattr(answer.requests, "post",
                        lambda *a, **kw: resp_with("NETLIST_FILE 用于指定网表路径，含格式与示例说明。\n引用来源: [1] GloryEX_2026.03_User_Guide.pdf"))
    ans, hit, grounded = _client().ask("NETLIST_FILE")
    assert hit and grounded and "NETLIST_FILE" in ans

    monkeypatch.setattr(answer.requests, "post", lambda *a, **kw: resp_with("根据提供的参考资料，无法回答该问题。"))
    ans, hit, grounded = _client().ask("随便")
    assert not hit and not grounded

    # 未正中但带引用来源 → grounded=True
    near = ('资料中未提及 "LEF_FILES"。相近的是 LEF_FILE_LIST 命令，用于指定 LEF 文件列表。\n'
            '引用来源: [1] GloryEX_2026.03_User_Guide.pdf')
    monkeypatch.setattr(answer.requests, "post", lambda *a, **kw: resp_with(near))
    ans, hit, grounded = _client().ask("LEF_FILES")
    assert not hit and grounded


def test_ask_empty(monkeypatch):
    import answer
    monkeypatch.setattr(answer.requests, "post",
                        lambda *a, **kw: _FakeResp('{"jsonrpc":"2.0","id":1,"result":{"content":[]}}'))
    with pytest.raises(KBError):
        _client().ask("q")
