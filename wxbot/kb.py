# -*- coding: utf-8 -*-
"""知识库读写客户端（薄封装，免@回复链路用）。

读：继承 answer.McpKBClient 的 ask()/search()（MCP kb_ask / kb_search）。
写：upload_markdown() 把群聊沉淀的 markdown 文档上传到网关。团队网关没有
    公开固定的上传工具名，默认按 kb_upload → kb_add_document → kb_add_file
    顺序试探；也可在配置 kb 段用 upload_tool 指定确切名字。
所有失败都抛 KBError（或原样抛出底层异常），由调用方静默降级——
KB 挂了只能少点参考/沉淀，绝不能阻断回复。
"""
import logging

from answer import KBError, McpKBClient

log = logging.getLogger("wxbot.kb")

_DEFAULT_UPLOAD_TOOLS = ("kb_upload", "kb_add_document", "kb_add_file")


class KBGateway(McpKBClient):
    """McpKBClient + 文档上传。构造入参与 McpKBClient 相同：{"mcp": <kb 配置段>}。"""

    def __init__(self, config):
        super().__init__(config)
        mcp = config.get("mcp") or {}
        tool = str(mcp.get("upload_tool") or "").strip()
        self.upload_tools = [tool] if tool else list(_DEFAULT_UPLOAD_TOOLS)

    def upload_markdown(self, title, markdown):
        """上传一份 markdown 文档，成功返回网关回执文本；所有候选工具都失败抛 KBError。"""
        args = {"kb": self.kb, "title": title,
                "filename": title + ".md", "content": markdown}
        errs = []
        for tool in self.upload_tools:
            try:
                text = self._call_tool(tool, dict(args))
                log.info("KB 上传成功(%s): %s", tool, title)
                return text
            except KBError as e:
                errs.append(tool + ": " + str(e))
        raise KBError("上传工具均不可用 → " + " | ".join(errs))
