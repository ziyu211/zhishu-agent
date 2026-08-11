"""文件系统工具（读 / 写 / 列目录，路径越界校验）。"""
from __future__ import annotations

import os

from ..base import tool
from . import SANDBOX_ROOT


def _safe_path(p: str) -> str:
    root = os.path.normpath(os.path.abspath(SANDBOX_ROOT))
    full = os.path.normpath(os.path.abspath(os.path.join(root, p)))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("路径越权")
    return full


@tool(
    "file_read",
    "读取文件内容。",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    toolset="files",
)
async def file_read(args: dict, ctx) -> str:
    path = _safe_path(args.get("path", ""))
    if not os.path.isfile(path):
        return f"文件不存在: {path}"
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# 图片扩展名（read_file 据此判断是否为图片；图片仅作视觉参考，不提取文字）
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".svg"}

# 二进制文件扩展名：file_write 是「文本模式写入」，绝不可用来生成这些文件，
# 否则会把内容当 UTF-8 文本写进二进制容器，生成 Excel/Word/PDF/图片等无法打开的损坏文件。
# .xlsx 等 Office 文件应走 generate_excel（或 code_exec + openpyxl）；其余走 code_exec。
_BINARY_WRITE_BLOCKED = {
    ".xlsx", ".xlsm", ".xls", ".docx", ".doc", ".pptx", ".ppt",
    ".pdf", ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".bz2", ".xz",
    ".odt", ".ods", ".odp", ".epub", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico",
    ".mp3", ".wav", ".ogg", ".opus", ".m4a", ".flac",
    ".mp4", ".mov", ".webm", ".avi", ".mkv",
}


def _resolve_read_path(path: str, owner: str | None = None,
                       is_admin: bool = False) -> str | None:
    """把各类路径解析为磁盘绝对路径，且必须落在**当前用户命名空间**内（防跨用户越权）。

    支持：/media/ URL、附件 stored_path（绝对路径，位于 media 目录内）、
    相对 media 或 sandbox 的路径、以及带 file:// 前缀的绝对路径（模型常返回此类）。

    安全（按用户收窄的白名单）：
      * 非管理员：仅允许 sandbox 目录，以及本人媒体目录
        —— media/<owner>/ 与 media/attachments/<owner>/
        （其他用户的 attachments 与 media 根均不可达，杜绝读他人附件）。
      * 管理员：额外允许整个 media 根（含所有用户的媒体，用于代管/审计）。
    白名单始终排除 data_dir 根与后端工作区根 —— 那里存放 providers.json（模型密钥）、
    users.json（口令哈希）、config.override.json、各 SQLite 库、按用户隔离的
    memory/ 目录以及后端源码，一律不得经 read_file 读取。
    """
    from ....context import get_ctx

    c = get_ctx()
    p = (path or "").strip()
    if p.startswith("file://"):
        p = p[len("file://"):]
    p = p.strip().strip('"').strip("'")
    # 统一转绝对，确保 stored_path（绝对）与 media/sandbox 前缀比对一致
    data_dir = os.path.abspath(c.cfg.server.data_dir)
    media_root = os.path.normpath(os.path.join(data_dir, c.cfg.media.store_dir))
    sb = os.path.abspath(SANDBOX_ROOT)
    if p.startswith("/media/"):
        p = p[len("/media/"):]
        cand = os.path.normpath(os.path.join(media_root, p))
    elif os.path.isabs(p):
        cand = os.path.normpath(p)
    else:
        in_media = os.path.normpath(os.path.join(media_root, p))
        in_data = os.path.normpath(os.path.join(data_dir, p))
        in_sb = os.path.normpath(os.path.join(sb, p))
        cand = next((x for x in (in_media, in_data, in_sb) if os.path.isfile(x)), in_sb)
    allowed = [sb]
    if is_admin:
        allowed.append(media_root)
    elif owner:
        allowed.append(os.path.join(media_root, owner))
        allowed.append(os.path.join(media_root, "attachments", owner))
    if not any(cand == a or cand.startswith(a + os.sep) for a in allowed):
        return None
    return cand


@tool(
    "read_file",
    "按需读取文件内容（对标 hermes 解耦/按需哲学），是读取用户上传文档的唯一入口。"
    "支持 TXT/MD/CSV/TSV/JSON/代码/日志等文本，以及 Word(.docx)/Excel(.xlsx)/PPT(.pptx)/"
    "OpenDocument(.odt/.ods/.odp)/RTF(.rtf)/EPUB(.epub)/PDF(.pdf) ——"
    "前者用标准库零依赖提取，无需任何第三方库。支持分页(page)、行号(start_line/end_line)、"
    "字符预算(max_chars)。**如需读取文件末尾最近 N 行（例如『提取最新100期』、查看日志尾部），"
    "请直接使用 tail 参数（如 tail: 100），无需先知道总行数。** 用于对话框附件或磁盘文件的按需解析，"
    "而非一次性全量解析。请勿使用 parse_docx/parse_xlsx/parse_pdf（已废弃）。图片请作为视觉参考传入模型，"
    "系统不内置 OCR，无法提取图片内文字。",
    {"type": "object", "properties": {
        "path": {"type": "string", "description": "文件路径：附件 stored_path、/media/ URL 或相对文件名"},
        "page": {"type": "integer", "description": "页码，从 1 开始，默认 1"},
        "page_size": {"type": "integer", "description": "每页行数，默认 800"},
        "max_chars": {"type": "integer", "description": "返回字符预算上限，默认 24000"},
        "start_line": {"type": "integer", "description": "起始行号（1 基），与 end_line 配合按行范围读取，优先于分页"},
        "end_line": {"type": "integer", "description": "结束行号（含），与 start_line 配合"},
        "tail": {"type": "integer", "description": "读取文件末尾最近 N 行（如 tail:100 取最新100期）；优先级高于 page/start_line/end_line"},
    }, "required": ["path"]},
    toolset="files",
)
async def read_file(args: dict, ctx) -> str:
    from ...rag import read_file_text, paginate_text, format_read

    # 兼容模型五花八门的参数名（尤其把 read_file 当「按行范围读取」工具时）
    path = (args.get("path") or args.get("file_path") or "").strip()
    if not path:
        return "[read_file] 缺少 path 参数"
    page = int(args.get("page") or args.get("page_number") or 1)
    page_size = int(args.get("page_size") or args.get("lines_per_page") or 800)
    max_chars = int(args.get("max_chars") or 24000)
    # 行范围（start_line/end_line 优先于分页，符合模型常见用法）
    start_line = args.get("start_line") or args.get("line_start") or args.get("line_begin")
    end_line = args.get("end_line") or args.get("line_end") or args.get("line_stop")
    # 兼容 offset/limit 组合（offset 视为 0 基起始行）
    if start_line is None and end_line is None:
        off = args.get("offset")
        lim = args.get("limit")
        if off is not None and lim is not None:
            try:
                start_line = int(off) + 1
                end_line = int(off) + int(lim)
            except (TypeError, ValueError):
                pass

    owner = getattr(ctx, "user", None)
    is_admin = getattr(ctx, "is_admin", False)
    abs_path = _resolve_read_path(path, owner, is_admin)
    if not abs_path or not os.path.isfile(abs_path):
        return f"[read_file] 文件不存在或越权: {path}"

    filename = os.path.basename(abs_path)
    ext = os.path.splitext(filename)[1].lower()

    # ── 图片：仅作视觉参考，系统不内置 OCR ──
    if ext in _IMG_EXTS:
        return (f"[read_file] {filename} 是图片。请作为视觉参考(vision)传入模型；"
                f"系统不内置 OCR，无法提取图片内文字。")

    # ── 文档：零依赖标准库提取（read_file_text 内部优先 stdlib）──
    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
        from ....context import get_ctx
        _c = get_ctx()
        if _c is not None:
            media_root = os.path.normpath(os.path.join(
                os.path.abspath(_c.cfg.server.data_dir), _c.cfg.media.store_dir))
        else:
            media_root = None
        text, ftype = read_file_text(filename, raw, media_root, owner)
    except ValueError as e:
        return f"[read_file] 解析失败: {e}"
    except Exception as e:  # noqa: BLE001
        return f"[read_file] 读取失败: {e}"
    # 归一化换行（兼容 Windows CRLF 文件，避免行尾残留 \\r 干扰解析/展示）
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return f"[read_file] {filename} 未提取到可解析文本（可能是图片型文档，请转换为文本型文档）。"
    # ── tail：取末尾最近 N 行（如「最新100期」），自动换算成行范围 ──
    tail_n = args.get("tail") or args.get("last_n") or args.get("last_lines")
    if tail_n is not None:
        try:
            nn = int(tail_n)
        except (TypeError, ValueError):
            nn = 0
        if nn > 0:
            # 去掉末尾换行再算行数，避免空行导致 tail 偏移（与 `tail -n` 语义一致）
            _t = text.rstrip("\n")
            total = len(_t.split("\n"))
            start_line = max(1, total - nn + 1)
            end_line = total
            text = _t
    pg = paginate_text(text, page, page_size, max_chars, start_line, end_line)
    return format_read(filename, ftype, pg)


@tool(
    "file_write",
    "写入文件并【自动】返回 /media/... 可下载链接。保存任何需要交付给用户的文件都必须使用本工具；"
    "它会把文件落盘到媒体库并直接返回下载链接，你只需把链接原样透传给用户。"
    "无需、也不得使用 downloadable 等选项关闭下载。",
    {"type": "object", "properties": {
        "path": {"type": "string", "description": "文件名（可含子目录，如 reports/工资表.txt）"},
        "content": {"type": "string"}},
     "required": ["path", "content"]},
    toolset="files",
)
async def file_write(args: dict, ctx) -> str:
    raw_path = args.get("path", "")
    ext = os.path.splitext(raw_path)[1].lower()
    if ext in _BINARY_WRITE_BLOCKED:
        hint = ("用 generate_excel 工具生成（支持多工作表 / CSV 输入）" if ext in (".xlsx", ".xlsm")
                else "用 code_exec 编写 Python 生成（如 python-docx 生成 docx、fpdf 生成 pdf），"
                     "生成的文件会自动发布为 /media 下载链接")
        return (f"[file_write] 拒绝写入 {ext} 二进制文件：file_write 是文本模式写入，"
                f"用它写 {ext} 会生成打不开的损坏文件。请{hint}。")
    path = _safe_path(raw_path)
    content = args.get("content", "")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # 默认发布为可下载文件（核心修复：不再因模型不传参数而只给路径）
    media = getattr(ctx, "media", None)
    if media is not None:
        name = os.path.basename(path)
        owner = getattr(ctx, "user", "anonymous") or "anonymous"
        try:
            url = media.save_file(path, kind="file", owner=owner)
        except Exception:
            return f"已保存 {len(content)} 字符为文件（下载链接生成失败，请使用 make_downloadable 补救）"
        return (f"已保存 {len(content)} 字符为可下载文件：\n"
                f"[{name}]({url})\n\n"
                f"（该链接真实有效、点击即可下载，你必须原样完整展示给用户，"
                f"禁止改写为『无法生成链接』『联系管理员』或『只给路径』等说法）")
    return f"已保存 {len(content)} 字符为文件：{os.path.basename(path)}"


@tool(
    "make_downloadable",
    "补救工具：把已存在的文件显式转换为可下载链接。传入 /media URL 或相对文件名，"
    "返回 [/media/... 下载链接]；若已是 /media 链接则原样返回。"
    "当某次产物未自动生成下载链接时，可用此工具兜底。",
    {"type": "object", "properties": {
        "path": {"type": "string", "description": "相对文件名、附件 stored_path 或 /media/ URL"}},
     "required": ["path"]},
    toolset="files",
)
async def make_downloadable(args: dict, ctx) -> str:
    p = (args.get("path") or "").strip()
    if not p:
        return "[make_downloadable] 缺少 path 参数"
    media = getattr(ctx, "media", None)
    if media is None:
        return "[make_downloadable] 当前环境不支持（无媒体存储）"
    if p.startswith("/media/"):
        return f"该文件已是下载链接：[{os.path.basename(p)}]({p})"
    owner = getattr(ctx, "user", None)
    is_admin = getattr(ctx, "is_admin", False)
    abs_path = _resolve_read_path(p, owner, is_admin)
    if not abs_path or not os.path.isfile(abs_path):
        return f"[make_downloadable] 文件不存在或越权: {p}"
    owner_str = getattr(ctx, "user", "anonymous") or "anonymous"
    try:
        url = media.save_file(abs_path, kind="file", owner=owner_str)
    except Exception as e:  # noqa: BLE001
        return f"[make_downloadable] 发布失败: {e}"
    name = os.path.basename(abs_path)
    return f"已生成可下载链接（点击即可下载，请原样展示给用户）：[{name}]({url})"


@tool(
    "file_list",
    "列出工作区文件。",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
    toolset="files",
)
async def file_list(args: dict, ctx) -> str:
    base = _safe_path(args.get("path", "."))
    if not os.path.isdir(base):
        return f"目录不存在: {base}"
    items = os.listdir(base)
    return "\n".join(items) or "(空)"
