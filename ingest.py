# -*- coding: utf-8 -*-
"""问答对沉淀导出。

知识库网关（zhipu-gateway-kb）只开放 kb_search/kb_ask，没有文档上传工具，
所以审核通过的问答对先导出为 markdown（exports/ 目录），由知识库管理员
导入团队问答库后，下次 kb_ask 即可命中。
"""
import logging
import time
from pathlib import Path

log = logging.getLogger("ingest")


class Ingestor:
    def __init__(self, config, db, export_dir=None):
        self.db = db
        self.export_dir = Path(export_dir or Path(__file__).parent / "exports")
        self.export_dir.mkdir(exist_ok=True)

    def ingest_approved(self, limit=20):
        """approved 问答对 → 追加到当日导出文件，状态置 exported。"""
        pairs = self.db.qa_pairs_by_status("approved", limit=limit)
        if not pairs:
            return 0, 0
        out = self.export_dir / f"qa_{time.strftime('%Y%m%d')}.md"
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"\n<!-- export {time.strftime('%Y-%m-%d %H:%M:%S')} -->\n")
            for p in pairs:
                f.write(f"## 问题 {p['id']}\n\n{p['q_text']}\n\n### 回答\n\n{p['a_text']}\n\n---\n")
                self.db.set_qa_status(p["id"], "exported")
                self.db.audit("ingest", "exported", f"#{p['id']} -> {out.name}")
        log.info("导出 %d 对到 %s", len(pairs), out)
        return len(pairs), len(pairs)
