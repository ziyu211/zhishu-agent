"""docx_revision —— 生成「带 Word 原生批注」的修订版 .docx（服务端稳定实现）。

为什么必须有这个工具
----------------------
「生成带批注的修订版 Word」此前完全靠模型在 code_exec 里手写底层 oxml：
构造 word/comments.xml + 改 [Content_Types].xml + 改 document.xml.rels +
fresh-write 重新打包 zip。对 240 份历史产物的实测：

    含 word/comments.xml（成功）:  52 份 = 22%
    失败                        : 188 份 = 78%
      157 份 压根没写批注逻辑（凭通用能力生成普通修订文档）
       28 份 只写锚点、缺 comments 部件（Word 里看不到批注）
        3 份 是 22 字节空 zip（打包中途异常留下的空壳）

把「能不能出批注」押在模型每次重新发明轮子，成功率只有 22%。本工具把格式与
批注下沉为服务端能力：模型只负责「找出错字」，其余由代码保证 100% 正确。
"""
from __future__ import annotations

import os

from ..base import tool


def _resolve_src(path: str, ctx, media) -> str | None:
    """解析源 docx 真实路径：优先走 file 工具的越权白名单，其次回退绝对路径/沙箱。"""
    if not path or not isinstance(path, str):
        return None
    owner = getattr(ctx, "user", None) or "anonymous"
    is_admin = bool(getattr(ctx, "is_admin", False))
    try:
        from .file import _resolve_read_path
        rp = _resolve_read_path(path, owner, is_admin)
        if rp and os.path.isfile(rp):
            return rp
    except Exception:
        pass
    f = path.strip()
    # 绝对路径
    if os.path.isabs(f) and os.path.isfile(f):
        return f
    # /media/... 链接
    if f.startswith("/media/"):
        root = getattr(media, "root", None)
        if root:
            cand = os.path.join(root, *f[len("/media/"):].strip("/").split("/"))
            if os.path.isfile(cand):
                return cand
    # 沙箱相对文件名
    try:
        from .sandbox import sandbox_cwd_for
        cand = os.path.join(sandbox_cwd_for(owner), os.path.basename(f))
        if os.path.isfile(cand):
            return cand
    except Exception:
        pass
    return None


@tool(
    "docx_revision",
    "【生成带批注的 Word 修订版 · 必须使用本工具】"
    "当用户要求「校对文档 / 生成修订版 / 标出错别字 / 带批注的 Word」时，用本工具生成："
    "错字=红色+删除线，正字=红色紧随其后，并带 **Word 原生批注**（右侧批注栏可见）。"
    "⚠️ 绝对不要用 code_exec 自己写 oxml 去构造 comments.xml 和重新打包 zip——"
    "实测那样做的产物 78% 没有批注（要么漏写批注部件，要么 zip 损坏打不开）。"
    "本工具由服务端用 python-docx + lxml 稳定生成，批注三件套（锚点/部件/关系注册）齐全，"
    "并已通过 zip 完整性校验，产物必定可被 Word 正常打开且批注可见。"
    "你只需要做认知部分：读懂文档、找出错误，然后把错误清单交给本工具。"
    "参数：**revisions 列表**，每项 {find: 原文错误片段, replace: 修正后文本, comment?: 批注说明}。"
    "  · replace 传**空字符串**表示「删除多余字」（如「隐私计算机」多出「机」，find='计算机', replace='计算'）；"
    "  · 漏字场景用 find=错误写法, replace=完整写法（如 find='字经济', replace='数字经济'）；"
    "  · comment 留空时自动生成「错别字修正：「X」→「Y」」。"
    "返回 /media/... 可点击下载链接，并告知修正处数、批注条数与未命中项。",
    {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "源 .docx 路径：附件的 stored_path（推荐）、/media/... 链接、绝对路径或沙箱文件名",
            },
            "revisions": {
                "type": "array",
                "description": "修订清单，每项 {find, replace, comment?}；find 为原文中待修正的片段，replace 为修正后文本（空串=删除多余字）",
                "items": {
                    "type": "object",
                    "properties": {
                        "find": {"type": "string", "description": "原文中的错误片段（用于在文档中定位）"},
                        "replace": {"type": "string", "description": "修正后的文本；空字符串表示删除多余字"},
                        "comment": {"type": "string", "description": "可选：批注文字，缺省自动生成"},
                    },
                    "required": ["find"],
                },
            },
            "filename": {"type": "string", "description": "输出文件名，缺省 <原名>_校对修订版.docx"},
            "author": {"type": "string", "description": "批注作者名，缺省『校对』"},
            "all_occurrences": {"type": "boolean", "description": "是否修正全文所有出现处，默认 true（同一错字多处均修）；传 false 则每处只改第一次出现"},
        },
        "required": ["path", "revisions"],
    },
    toolset="files",
)
async def docx_revision(args: dict, ctx) -> str:
    media = getattr(ctx, "media", None)
    if media is None:
        return "[docx_revision] 当前环境不支持（无媒体存储）"

    path = (args.get("path") or "").strip()
    if not path:
        return "[docx_revision] 缺少 path（源 .docx 路径）"

    revisions = args.get("revisions")
    if isinstance(revisions, dict):
        revisions = [revisions]
    if not revisions or not isinstance(revisions, list):
        return "[docx_revision] 缺少 revisions（修订清单，每项 {find, replace, comment?}）"

    src = _resolve_src(path, ctx, media)
    if not src:
        return f"[docx_revision] 找不到源文档（或路径越权）: {path}"

    try:
        from ...docx_revision import build_revised_docx, DocxRevisionError
    except Exception as e:  # 依赖缺失
        return f"[docx_revision] 模块加载失败：{e}"

    owner = getattr(ctx, "user", "anonymous") or "anonymous"
    author = (args.get("author") or "校对").strip() or "校对"
    base = os.path.splitext(os.path.basename(src))[0]
    filename = (args.get("filename") or f"{base}_校对修订版.docx").strip()
    if not filename.lower().endswith(".docx"):
        filename += ".docx"
    all_occ = args.get("all_occurrences")
    all_occ = True if all_occ is None else bool(all_occ)

    try:
        data, stats = build_revised_docx(
            src, revisions, author=author, all_occurrences=all_occ)
    except DocxRevisionError as e:
        return f"[docx_revision] 生成失败：{e}"
    except Exception as e:
        return f"[docx_revision] 生成异常：{type(e).__name__}: {e}"

    try:
        url = media.save_bytes(data, kind="file", ext="docx", owner=owner)
    except Exception as e:
        return f"[docx_revision] 产物发布失败：{e}"

    lines = [
        f"[docx_revision] 已生成带 Word 原生批注的修订版：{filename}",
        f"- 下载链接：{url}",
        f"- 修正 {stats['applied']} 处 / 批注 {stats['comments']} 条"
        f"（扫描段落 {stats['paragraphs']} 个）",
    ]
    if stats.get("missed"):
        lines.append(
            f"- ⚠️ 以下片段在原文中**未找到**，请核对原文后调整 find："
            f"{'、'.join(stats['missed'][:10])}")
    lines.append("- 格式：错字=红色+删除线，正字=红色紧随其后；右侧批注栏查看说明。")
    return "\n".join(lines)
