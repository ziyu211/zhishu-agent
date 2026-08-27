"""回归测试：WPS 旧版格式（.wps/.et/.dps）解析派发（全量优化 T6）。

验证：
  1. _TRUSTED_EXTS 已纳入 .wps/.et/.dps（与 .doc/.xls/.ppt 同属旧版 OLE 二进制）；
  2. read_file_text 对 .wps/.et/.dps 走「LibreOffice 优先 + 字节扫描兜底 + 引导另存」
     的确定分支，而非被嗅探/通用分支吞掉；在无 LO 且无有效文本时给出可操作的中文报错。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zhishu.core.rag as rag
from zhishu.core.rag import read_file_text

passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {msg}")
    else:
        failed += 1
        print(f"  [FAIL] {msg}")


def test_trusted_exts():
    exts = rag._TRUSTED_EXTS
    for e in (".wps", ".et", ".dps"):
        check(e in exts, f"_TRUSTED_EXTS 包含 {e}")


def test_wps_dispatch_informative_error():
    # 让 LibreOffice 转换与「尽力而为」字节扫描都返回无效，强制进入可操作报错分支，
    # 避免测试环境依赖 soffice / 真实 OLE 解析。
    orig_conv = rag._libreoffice_convert
    orig_best = rag._extract_legacy_best_effort
    rag._libreoffice_convert = lambda *a, **k: None
    rag._extract_legacy_best_effort = lambda *a, **k: ""
    try:
        for ext, label in ((".wps", "WPS"), (".et", "ET"), (".dps", "DPS")):
            fname = f"demo{ext}"
            raw = os.urandom(128)  # 非真实 OLE，保证无有效文本
            try:
                text, ftype = read_file_text(fname, raw, "/tmp/zhishu_media", "tester")
                # 若居然解析出文本，应当非空且 file_type 命中该格式
                check(bool(text.strip()) and ftype == label,
                      f"{ext} 解析出文本且 file_type={label}（LO 意外可用）")
            except ValueError as e:
                msg = str(e)
                check(("WPS" in msg or "另存为" in msg or ext in msg),
                      f"{ext} 抛出可操作的中文报错（含 WPS/另存为 指引）: {msg[:40]}...")
    finally:
        rag._libreoffice_convert = orig_conv
        rag._extract_legacy_best_effort = orig_best


if __name__ == "__main__":
    print("== WPS 旧版格式派发测试 ==")
    test_trusted_exts()
    test_wps_dispatch_informative_error()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
