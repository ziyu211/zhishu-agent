"""知识库检索 / 文档读取工具（打通"上传→提取"）。"""
from __future__ import annotations

from ..base import tool


@tool(
    "knowledge_search",
    "检索本地知识库（RAG），返回与问题最相关的内部文档片段。",
    {"type": "object", "properties": {
        "question": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["question"]},
    toolset="knowledge",
)
async def knowledge_search(args: dict, ctx) -> str:
    if not ctx.kb:
        return "[提示] 未初始化知识库。"
    hits = ctx.kb.query(args.get("question", ""), int(args.get("top_k", 5)))
    if not hits:
        return "知识库暂无相关结果。"
    out = []
    for i, h in enumerate(hits, 1):
        out.append(f"[{i}] 来源:{h['meta'].get('source', h['doc_id'])} 相似度:{h['score']:.3f}\n{h['text']}")
    return "\n\n".join(out)


@tool(
    "knowledge_list",
    "列出知识库中的文档清单（标题、类型、大小、归属）。"
    "当用户说'提取/查看/阅读/总结文档内容'而尚未指明具体文档时，应先调用本工具定位文档。",
    {"type": "object", "properties": {
        "keyword": {"type": "string", "description": "可选：按标题/文件名关键字过滤"}}, "required": []},
    toolset="knowledge",
)
async def knowledge_list(args: dict, ctx) -> str:
    if not ctx.kb:
        return "[提示] 未初始化知识库。"
    docs = ctx.kb.list_documents(owner=ctx.user, limit=200, offset=0)
    if not docs:
        return "知识库为空，请先上传文件或粘贴文本。"
    kw = (args.get("keyword") or "").lower()
    if kw:
        docs = [d for d in docs if kw in (d.get("title") or "").lower()
                or kw in (d.get("source") or "").lower()
                or kw in (d.get("doc_id") or "").lower()]
    if not docs:
        return f"知识库中没有匹配「{kw}」的文档。"
    lines = []
    for d in docs:
        meta = d.get("meta") or {}
        title = (meta.get("title") or d.get("title") or d.get("doc_id"))
        ftype = (meta.get("file_type") or d.get("file_type") or "?")
        size = (meta.get("size") or d.get("size_bytes") or 0)
        lines.append(f"- doc_id={d['doc_id']} | 标题:{title} | 类型:{ftype} | 大小:{size}字节")
    return "知识库文档清单：\n" + "\n".join(lines)


@tool(
    "knowledge_read",
    "读取知识库中某篇文档的完整内容（全文）。先通过 knowledge_list 获取 doc_id 或标题，"
    "再调用本工具提取全文供整理/摘要/问答。支持按 doc_id 或标题匹配。",
    {"type": "object", "properties": {
        "doc_id": {"type": "string", "description": "文档 ID；与 title 二选一"},
        "title": {"type": "string", "description": "文档标题或文件名（模糊匹配）"}}, "required": []},
    toolset="knowledge",
)
async def knowledge_read(args: dict, ctx) -> str:
    if not ctx.kb:
        return "[提示] 未初始化知识库。"
    doc_id = (args.get("doc_id") or "").strip()
    title = (args.get("title") or "").strip()
    if not doc_id and not title:
        return "[提示] 请提供 doc_id 或 title。可先调用 knowledge_list 获取。"
    doc = None
    if doc_id:
        doc = ctx.kb.get_document(doc_id, owner=ctx.user)
    if not doc and title:
        for d in ctx.kb.list_documents(owner=ctx.user, limit=200, offset=0):
            meta = d.get("meta") or {}
            t = (meta.get("title") or d.get("title") or "")
            src = (meta.get("source") or "")
            if title.lower() in t.lower() or title.lower() in src.lower():
                doc = ctx.kb.get_document(d["doc_id"], owner=ctx.user)
                break
    if not doc:
        return f"[未找到] 未匹配到文档（doc_id={doc_id or '空'}, title={title or '空'}）。请先用 knowledge_list 查看可用文档。"
    meta = doc.get("meta") or {}
    title_out = (meta.get("title") or doc.get("title") or doc.get("doc_id"))
    content = doc.get("content") or meta.get("content_preview") or meta.get("text") or ""
    if not content.strip():
        return f"[空文档] 文档「{title_out}」未提取到文本内容（可能为图片型或未解析成功）。"
    return f"【文档：{title_out}】\n{content}"
