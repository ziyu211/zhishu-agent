"""文件比对工具：结构化比对两个文件（Excel / CSV / TSV / 文本 / PDF 等）。

设计要点：
  * 路径在服务端解析（复用 read_file 的命名空间白名单，防跨用户越权），模型只需传入
    附件的 stored_path / /media/ URL / 完整 http(s)://.../media/... / 文件名，无需手抄长路径。
  * 比对在服务端完成：Excel/CSV 用 pandas 做工作表/列/行级结构化差异；文本/PDF 抽取后做行级 diff。
  * 输出人类可读的 markdown 差异报告；output='file' 时额外落盘报告并返回 /media/ 下载链接。
对标 hermes 的「文件对比」能力，但更贴合多租户内网部署（路径不出后端）。
"""
from __future__ import annotations

import difflib
import os

from ..base import tool
from .file import _resolve_read_path  # 复用路径解析 + 用户命名空间白名单

_SAMPLE_ROWS = 60          # 单表最多展示的差异行数
_MAX_REPORT = 12000        # 报告字符上限，超出截断并提示


def _truncate(cell, n: int = 120) -> str:
    s = "" if cell is None else str(cell)
    return s if len(s) <= n else s[: n - 1] + "…"


def _rows_as_tuples(df) -> list[tuple]:
    """把 DataFrame 的每一行转成可哈希元组（用于内容级集合差，忽略行序）。"""
    out = []
    for _, row in df.iterrows():
        out.append(tuple(_truncate(v, 200) for v in row.tolist()))
    return out


def _load_table(path: str, owner=None):
    """加载为 {sheet: DataFrame}；返回 (sheets, err)。仅处理 Excel / CSV / TSV。"""
    import pandas as pd

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm", ".xls"):
            xls = pd.ExcelFile(path)
            sheets = {s: xls.parse(s, dtype=str, keep_default_na=False) for s in xls.sheet_names}
            return sheets, None
        if ext in (".csv", ".tsv"):
            sep = "\t" if ext == ".tsv" else ","
            df = pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False)
            return {"Sheet1": df}, None
    except Exception as e:  # noqa: BLE001
        return None, f"表格读取失败: {e}"
    return None, f"非表格文件类型: {ext}"


def _extract_text(path: str, owner=None):
    """通用文本抽取（PDF/文本/代码等），返回 (text, err)。"""
    from ....context import get_ctx

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:  # noqa: BLE001
        return None, f"读取失败: {e}"
    c = get_ctx()
    if c is not None:
        media_root = os.path.normpath(os.path.join(
            os.path.abspath(c.cfg.server.data_dir), c.cfg.media.store_dir))
    else:
        media_root = None
    try:
        from ...rag import read_file_text

        text, _ = read_file_text(os.path.basename(path), raw, media_root, owner)
    except Exception as e:  # noqa: BLE001
        return None, f"文本抽取失败: {e}"
    if not text.strip():
        return None, "未提取到可解析文本（可能是图片型文档，无法文本比对）"
    return text, None


def _diff_tables(a_sheets: dict, b_sheets: dict, sa: str | None, sb: str | None) -> list[str]:
    lines: list[str] = []
    a_names, b_names = list(a_sheets), list(b_sheets)
    # 按指定 sheet 过滤（若给定且存在）
    if sa and sa in a_sheets:
        a_sheets = {sa: a_sheets[sa]}
        a_names = [sa]
    if sb and sb in b_sheets:
        b_sheets = {sb: b_sheets[sb]}
        b_names = [sb]
    only_a = [s for s in a_names if s not in b_sheets]
    only_b = [s for s in b_names if s not in a_sheets]
    common = [s for s in a_names if s in b_sheets]

    lines.append(f"- 工作表：A={a_names}  B={b_names}")
    if only_a:
        lines.append(f"- 仅 A 存在的工作表：{only_a}")
    if only_b:
        lines.append(f"- 仅 B 存在的工作表：{only_b}")

    for s in common:
        da, db = a_sheets[s], b_sheets[s]
        lines.append(f"\n### 工作表「{s}」")
        ca, cb = list(da.columns), list(db.columns)
        cols_only_a = [c for c in ca if c not in cb]
        cols_only_b = [c for c in cb if c not in ca]
        if cols_only_a or cols_only_b:
            lines.append(f"- 列差异：仅A={cols_only_a or '无'}  仅B={cols_only_b or '无'}")
            # 统一列后比对
            common_cols = [c for c in ca if c in cb]
            da = da[common_cols]
            db = db[common_cols]
        ra, rb = _rows_as_tuples(da), _rows_as_tuples(db)
        set_a, set_b = set(ra), set(rb)
        only_a_rows = [r for r in ra if r not in set_b]
        only_b_rows = [r for r in rb if r not in set_a]
        lines.append(f"- 行数：A={len(ra)}  B={len(rb)}；"
                     f"内容独有A={len(only_a_rows)}  独有B={len(only_b_rows)}")
        for r in only_a_rows[:_SAMPLE_ROWS]:
            lines.append(f"  - [A独有] {r}")
        for r in only_b_rows[:_SAMPLE_ROWS]:
            lines.append(f"  + [B独有] {r}")
        if len(only_a_rows) > _SAMPLE_ROWS or len(only_b_rows) > _SAMPLE_ROWS:
            lines.append(f"  …（差异行较多，仅展示前 {_SAMPLE_ROWS} 条）")
    return lines


def _diff_text(a_text: str, b_text: str) -> list[str]:
    la = a_text.splitlines()
    lb = b_text.splitlines()
    diff = difflib.unified_diff(la, lb, fromfile="文件A", tofile="文件B", lineterm="")
    out = list(diff)
    if not out:
        return ["两个文本文件内容完全一致（无差异）。"]
    return ["```diff", *out[:_SAMPLE_ROWS * 3], "```"]


@tool(
    "compare_files",
    "比对两个文件并输出结构化差异报告。支持 Excel(.xlsx/.xls/.xlsm)、CSV/TSV、文本/代码/PDF 等。"
    "Excel/CSV 按工作表→列→行做结构化比对（内容级集合差，忽略行序）；文本/PDF 抽取后做行级 diff。"
    "**这是比对文件的唯一正确入口**：传入两个文件的引用（附件 stored_path、/media/ URL、"
    "完整 http(s)://.../media/... 或文件名）即可，无需手抄长路径，也无需自行 read_file 后人工对比。"
    "比对在服务端完成，结果以可读报告返回；output='file' 时额外生成报告文件并返回 /media/ 下载链接。",
    {"type": "object", "properties": {
        "file_a": {"type": "string", "description": "文件A引用：stored_path、/media/ URL、完整 http(s)://.../media/... 或文件名"},
        "file_b": {"type": "string", "description": "文件B引用（同上）"},
        "sheet_a": {"type": "string", "description": "Excel 文件A指定的工作表名（可选，默认全部/第一张）"},
        "sheet_b": {"type": "string", "description": "Excel 文件B指定的工作表名（可选）"},
        "output": {"type": "string", "description": "'text'(默认，直接返回报告) 或 'file'(额外写报告文件并返回 /media/ 下载链接)"},
    }, "required": ["file_a", "file_b"]},
    toolset="files",
)
async def compare_files(args: dict, ctx) -> str:
    ref_a = (args.get("file_a") or "").strip()
    ref_b = (args.get("file_b") or "").strip()
    if not ref_a or not ref_b:
        return "[compare_files] 必须提供 file_a 与 file_b 两个文件引用（stored_path / /media/ URL / 文件名）。"

    owner = getattr(ctx, "user", None)
    is_admin = getattr(ctx, "is_admin", False)

    def _resolve(ref: str) -> str | None:
        p = ref.strip().strip('"').strip("'")
        if p.startswith("http://") or p.startswith("https://"):
            idx = p.find("/media/")
            if idx != -1:
                p = p[idx:]
        return _resolve_read_path(p, owner, is_admin)

    pa, pb = _resolve(ref_a), _resolve(ref_b)
    if not pa or not os.path.isfile(pa):
        return f"[compare_files] 文件A无法解析或越权: {ref_a}"
    if not pb or not os.path.isfile(pb):
        return f"[compare_files] 文件B无法解析或越权: {ref_b}"

    na, nb = os.path.basename(pa), os.path.basename(pb)
    lines = [f"# 文件比对报告", f"- A：`{na}`", f"- B：`{nb}`", ""]

    # 先尝试按表格加载
    a_tbl, a_err = _load_table(pa, owner)
    b_tbl, b_err = _load_table(pb, owner)

    if a_tbl is not None and b_tbl is not None:
        lines.append("**类型**：表格（Excel/CSV/TSV）")
        lines += _diff_tables(a_tbl, b_tbl, args.get("sheet_a"), args.get("sheet_b"))
    else:
        # 退化为文本/PDF 行级 diff
        at, a_err2 = (a_tbl, a_err) if a_tbl is not None else _extract_text(pa, owner)
        bt, b_err2 = (b_tbl, b_err) if b_tbl is not None else _extract_text(pb, owner)
        if at is None or bt is None:
            return (f"[compare_files] 无法解析文件内容：\n"
                    f"A: {a_err or a_err2}\nB: {b_err or b_err2}")
        lines.append("**类型**：文本/PDF（行级 diff）")
        lines += _diff_text(at, bt)

    report = "\n".join(lines)
    if len(report) > _MAX_REPORT:
        report = report[:_MAX_REPORT] + "\n\n…（报告过长已截断，请缩小比对范围或指定 sheet 后重试）"

    # 可选：落盘为报告文件并返回下载链接
    if (args.get("output") or "text") == "file":
        media = getattr(ctx, "media", None)
        if media is not None:
            try:
                url = media.save_bytes(
                    report.encode("utf-8"), kind="file", ext="md",
                    owner=(owner or "anonymous"))
                report += f"\n\n[完整报告下载]({url})"
            except Exception:  # noqa: BLE001
                report += "\n\n（报告文件生成失败，以上为内联内容）"
    return report
