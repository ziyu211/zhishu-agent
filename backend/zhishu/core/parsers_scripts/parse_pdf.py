"""智枢解析插件脚本 —— PDF 文本提取（委托标准库/内置库，零冗余依赖）。

    python parse_pdf.py <文件路径>

统一复用 zhishu.core.rag.read_file_text 的 PDF 提取逻辑（pypdf 优先、
pdfminer 兜底），保证「安装即可用、调用必返回内容」。
无论模型调用 read_file 还是 parse_pdf，都能拿到一致的正文文本。
对纯图片扫描件若内置 OCR(tesseract+中文包) 仍无文字，会返回空文本（扫描件请作为视觉参考或转换为文本型 PDF）。
"""
from __future__ import annotations

import os
import sys


def _backend_dir() -> str:
    # 脚本位于 backend/data/plugins/parser-xxx/ 下，向上回溯直到找到含 zhishu 包的目录
    d = os.path.dirname(os.path.abspath(__file__))
    while d and d != os.path.dirname(d):
        if os.path.isfile(os.path.join(d, "zhishu", "__init__.py")):
            return d
        d = os.path.dirname(d)
    raise RuntimeError("找不到 backend 目录（含 zhishu 包）")


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("用法：python parse_pdf.py <文件路径>\n")
        return 2
    path = sys.argv[1]
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        sys.stderr.write(f"无法读取文件：{e}\n")
        return 2

    backend = _backend_dir()
    if backend not in sys.path:
        sys.path.insert(0, backend)
    try:
        from zhishu.core.rag import read_file_text
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"加载解析器失败：{e}\n")
        return 2

    try:
        text, _ftype = read_file_text(os.path.basename(path), raw)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"PDF 解析失败：{e}\n")
        return 2

    if not text.strip():
        sys.stderr.write("PDF 未提取到文本：可能已加密、受损，或为纯图片扫描件（内置 OCR 仍无有效文字）。\n")
        return 3
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"PDF 解析失败：{e}\n")
        raise SystemExit(2)
