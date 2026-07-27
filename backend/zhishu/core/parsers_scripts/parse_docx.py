"""智枢解析插件脚本 —— Word(.docx) 文本提取（委托标准库，零依赖）。

    python parse_docx.py <文件路径>

统一复用 zhishu.core.rag.read_file_text 的零依赖标准库提取逻辑，
不再依赖 python-docx，保证「安装即可用、调用必返回内容」。
无论模型调用 read_file 还是 parse_docx，都能拿到一致的正文/表格文本。
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
        sys.stderr.write("用法：python parse_docx.py <文件路径>\n")
        return 2
    path = sys.argv[1]
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        sys.stderr.write(f"无法读取文件：{e}\n")
        return 2

    # 把 backend 目录加入路径以便 import zhishu
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
        sys.stderr.write(f"DOCX 解析失败：{e}\n")
        return 2

    if not text.strip():
        sys.stderr.write("Word 文档未提取到文本（可能是图片型文档，请转换为文本型文档）。\n")
        return 3
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"DOCX 解析失败：{e}\n")
        raise SystemExit(2)
