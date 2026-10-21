# -*- coding: utf-8 -*-
from mining.detector import is_question


def test_question_forms():
    assert is_question("请问 NETLIST_FILE 命令怎么使用？")
    assert is_question("GloryEX 导出 spef 的命令是什么")
    assert is_question("为什么我的 extraction 报错了")
    assert is_question("有人遇到过 license 不生效吗")
    assert is_question("a01 工艺的 map 文件和 table 文件在哪里下载？")


def test_non_questions():
    assert not is_question("NETLIST_FILE")            # 短命令名
    assert not is_question("a01.table")
    assert not is_question("好的")
    assert not is_question("这个设计已经跑完了，结果在服务器上")
    assert not is_question("")
