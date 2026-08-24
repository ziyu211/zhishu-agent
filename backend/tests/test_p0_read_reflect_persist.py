"""P0 提速/防呆回归测试（v1.0.37）。

覆盖：
  A. reflection_enabled 默认开启（config 默认 True）。
  B. read_file 大预算 + 续读：paginate_text 超预算截断返回 next_offset；
     format_read 截断时给出 start_line 续读指令。
  C. 大结果自动落盘：persist_long_output 超阈值落盘媒体库返回下载链接；
     未超阈值原样返回；无媒体库时截断兜底。

运行：python tests/test_p0_read_reflect_persist.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.config import ZhishuConfig  # noqa: E402
from zhishu.core.rag import paginate_text, format_read  # noqa: E402
from zhishu.core.tools.builtins import artifacts  # noqa: E402

PASS = 0
FAIL = []


def check(cond, name):
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}")


def test_reflection_default_on():
    print("\n[A] reflection_enabled 默认开启")
    cfg = ZhishuConfig()
    check(cfg.agent.reflection_enabled is True,
          "ZhishuConfig().agent.reflection_enabled == True（默认开启）")


def test_paginate_next_offset():
    print("\n[B] paginate_text 超预算截断返回 next_offset")
    # 200 行，每行 100 字符 → 远超任何预算
    lines = [f"line_{i:03d}_" + "x" * 90 for i in range(200)]
    text = "\n".join(lines)
    pg = paginate_text(text, page=1, page_size=800, max_chars=3000)
    check(pg["truncated"] is True, "分页模式超预算标记 truncated")
    check(pg.get("next_offset") is not None and pg["next_offset"] > 1,
          f"分页模式返回 next_offset={pg.get('next_offset')}（续读行号）")
    check("已按字符预算截断" in pg["block"], "block 被字符预算截断")
    # 行范围模式
    pg2 = paginate_text(text, max_chars=2000, start_line=1, end_line=200)
    check(pg2["truncated"] is True, "行范围模式超预算标记 truncated")
    check(pg2.get("next_offset") is not None and pg2["next_offset"] > 1,
          f"行范围模式返回 next_offset={pg2.get('next_offset')}")
    # 未超预算时不带 next_offset
    pg3 = paginate_text(text, page=1, page_size=5, max_chars=100000)
    check(pg3["truncated"] is False, "未超预算 truncated=False")
    check(pg3.get("next_offset") is None, "未超预算 next_offset=None")


def test_format_read_next_offset_hint():
    print("\n[B2] format_read 截断时给出 start_line 续读指令")
    pg = {"block": "1: abc", "total_lines": 200, "page": 1, "page_total": 1,
          "page_lines": 1, "truncated": True, "next_offset": 50}
    out = format_read("big.log", "text", pg)
    check("start_line=50" in out, "format_read 含 start_line=50 续读指令")
    check("max_chars" in out, "format_read 提示可调大 max_chars")
    # 未截断但有更多页 → 通用定位提示（不强制 next_offset 数字）
    pg2 = {"block": "1: abc", "total_lines": 200, "page": 1, "page_total": 10,
           "page_lines": 1, "truncated": False, "next_offset": None}
    out2 = format_read("big.log", "text", pg2)
    check("start_line=50" not in out2, "未截断不误导用 start_line=50")
    check("第 1/10 页" in out2, "分页信息保留")


class _FakeMedia:
    """最小 MediaStore 桩：save_file 落盘到临时目录并返回 URL。"""

    def __init__(self, root: str):
        self.root = root
        self.url_prefix = "/media"
        self.saved = []

    def save_file(self, src_path: str, kind: str = "file", owner: str = None) -> str:
        os.makedirs(self.root, exist_ok=True)
        name = f"r_{len(self.saved)}_{os.path.basename(src_path)}"
        shutil.copy(src_path, os.path.join(self.root, name))
        self.saved.append(name)
        return f"{self.url_prefix}/{name}"


def test_persist_long_output():
    print("\n[C] persist_long_output 大结果落盘")
    tmp = tempfile.mkdtemp()
    try:
        media = _FakeMedia(tmp)
        short = "short output"
        check(artifacts.persist_long_output(short, media, "alice") == short,
              "未超阈值原样返回（不落盘）")
        check(len(media.saved) == 0, "未超阈值不产生媒体文件")

        long_text = "A" * 30000
        out = artifacts.persist_long_output(long_text, media, "alice")
        check("/media/" in out, "超阈值落盘并返回 /media 链接")
        check("完整输出已存盘" in out, "落盘提示存在")
        check(len(media.saved) == 1, "落盘产生 1 个媒体文件")
        check("read_file" in out, "落盘提示含 read_file 续读建议")
        check(len(out) < 5000, "返回体被预览截断（不塞全文）")

        # 无媒体库 → 截断兜底
        out2 = artifacts.persist_long_output(long_text, None, "alice")
        check("(输出过长已截断)" in out2, "无媒体库时截断兜底")
        check("完整输出 30000 字符" in out2, "无媒体库提示全文长度")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_reflection_default_on()
    test_paginate_next_offset()
    test_format_read_next_offset_hint()
    test_persist_long_output()
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")
