"""回归测试：工具参数退化 JSON 修复（缺口②）。

场景来源：模型恢复 create_skill / create_tool 调用时产生非法 JSON（智能引号、
字符串内未转义换行、尾随逗号、括号被截断），原逻辑直接置 {} 丢光 name/content
导致工具必失败、模型反复重试仍畸形。修复后应在丢弃前尝试修复。
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.agent.json_repair import repair_tool_args  # noqa: E402


def _check(raw, expect_dict, label):
    out = repair_tool_args(raw)
    try:
        parsed = json.loads(out) if out is not None else None
    except (json.JSONDecodeError, TypeError):
        print(f"[FAIL] {label}: 修复结果仍非合法 JSON -> {out!r}")
        return False
    if parsed != expect_dict:
        print(f"[FAIL] {label}: 解析结果 {parsed!r} != 期望 {expect_dict!r}")
        return False
    print(f"[OK]   {label}")
    return True


def _check_none(raw, label):
    out = repair_tool_args(raw)
    if out is not None:
        print(f"[FAIL] {label}: 期望 None，实际 {out!r}")
        return False
    print(f"[OK]   {label}")
    return True


def main():
    ok = True

    # 1. 合法 JSON 原样放行
    ok &= _check('{"name":"svip","content":"x"}',
                {"name": "svip", "content": "x"}, "合法 JSON 原样放行")

    # 2. 智能/全角双引号当作定界符
    ok &= _check('{"name":"SVIPFTP 自动处理","content":"# 概述"}',
                {"name": "SVIPFTP 自动处理", "content": "# 概述"},
                "智能双引号修复")

    # 3. 字符串内未转义裸换行
    ok &= _check('{"content":"line1\nline2"}',
                {"content": "line1\nline2"}, "字符串内裸换行转义")

    # 4. 尾随逗号
    ok &= _check('{"a":1,"b":2,}',
                {"a": 1, "b": 2}, "尾随逗号修复")

    # 5. 括号不闭合（被截断）
    ok &= _check('{"name":"svip","content":"hello',
                {"name": "svip", "content": "hello"}, "括号不闭合补齐")

    # 6. 全角逗号/冒号混入 key-value
    ok &= _check('{"name"："svip"，"content"："x"}',
                {"name": "svip", "content": "x"}, "全角冒号/逗号修复")

    # 7. 真实 SVIPFTP 形态：智能引号 + 多行 content（含裸换行）
    raw = '{"name"\uff1a"SVIPFTP \u81ea\u52a8\u5904\u7406","description"\uff1a"\u81ea\u52a8\u4eceFTP\u4e0b\u8f7d","content"\uff1a"# \u6982\u8ff0\n\u81ea\u52a8\u4eceFTP\u670d\u52a1\u5668\u4e0b\u8f7d\u6700\u65b0\u6587\u6863"}'
    ok &= _check(raw,
                {"name": "SVIPFTP 自动处理",
                 "description": "自动从FTP下载",
                 "content": "# 概述\n自动从FTP服务器下载最新文档"},
                "SVIPFTP 真实形态（智能引号+全角冒号+裸换行）")

    # 8. 完全不可修复（空 / 乱码）
    ok &= _check_none("", "空串 -> None")
    ok &= _check_none("   ", "纯空白 -> None")
    ok &= _check_none("not json at all ###", "纯乱码 -> None")

    if ok:
        print("\nALL REPAIR TESTS PASSED")
        sys.exit(0)
    else:
        print("\nSOME REPAIR TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
