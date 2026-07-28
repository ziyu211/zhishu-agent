"""智枢智能体 —— 知识库路由（RAG 离线）。

端点：
  POST /ingest                      文本入库（可指定标题/文档ID/归属人）
  POST /upload                      文件上传（自动解析文本类/PDF/Word/Excel）
  GET  /documents?scope=mine|all    文档列表（普通用户看自己+共享，管理员看全部）
  GET  /documents/{doc_id}          文档详情 + 预览正文
  DELETE /documents/{doc_id}        删除文档（私有文档仅归属人/管理员可删）
  GET  /search?q=...&top_k=         检索（仅检索可见文档）
  GET  /stats                       统计（按可见范围）
"""
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from pydantic import BaseModel

from .auth import require_auth
from ..context import get_ctx

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class IngestReq(BaseModel):
    text: str
    doc_id: str | None = None
    title: str | None = None


@router.post("/ingest")
async def ingest(req: IngestReq, user=require_auth("knowledge:read")):
    ctx = get_ctx()
    if not req.text.strip():
        return {"ok": False, "msg": "文本为空"}
    owner = user.get("u")
    try:
        res = ctx.kb.ingest_text(
            req.text, doc_id=req.doc_id, owner=owner, title=req.title
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"入库失败：{e}")
    if res.get("skipped"):
        return {"ok": True, **res, "msg": "内容为空，已跳过"}
    ctx.audit.log(owner or "anon", "knowledge:ingest", f"doc_id={res['doc_id']}")
    return {"ok": True, **res}


@router.post("/upload")
async def upload(file: UploadFile = File(...), user=require_auth("knowledge:read")):
    ctx = get_ctx()
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    owner = user.get("u")
    try:
        res = ctx.kb.ingest_file(file.filename or "未命名文件", raw, owner=owner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # 兜底：任何意外都不应裸 500
        raise HTTPException(status_code=500, detail=f"解析失败：{e}")
    ctx.audit.log(owner or "anon", "knowledge:upload", file.filename or "?")
    return {"ok": True, **res}


@router.get("/documents")
async def list_documents(
    scope: str = Query("mine", pattern="^(mine|all)$"),
    q: str = Query("", description="按标题/来源关键字过滤"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user=require_auth("knowledge:read"),
):
    ctx = get_ctx()
    owner = None if (scope == "all" and user.get("r") == "admin") else user.get("u")
    docs = ctx.kb.list_documents(owner=owner, limit=limit, offset=offset, q=q or None)
    return {"documents": docs, "scope": scope, "owner": owner,
            "total": ctx.kb.store.doc_count(owner, q or None)}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, user=require_auth("knowledge:read")):
    ctx = get_ctx()
    owner = None if user.get("r") == "admin" else user.get("u")
    doc = ctx.kb.get_document(doc_id, owner=owner)
    if not doc:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="文档不存在或无权限")
    return doc


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, user=require_auth("knowledge:read")):
    ctx = get_ctx()
    owner = None if user.get("r") == "admin" else user.get("u")
    ok = ctx.kb.delete_document(doc_id, owner=owner)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="文档不存在或无权限")
    ctx.audit.log(user.get("u", "anon"), "knowledge:delete", doc_id)
    return {"ok": True}


@router.post("/documents/{doc_id}/reparse")
async def reparse_document(doc_id: str, user=require_auth("knowledge:read")):
    """用保留的原始文件，以当前解析器重新提取并覆盖入库（doc_id 不变）。"""
    ctx = get_ctx()
    owner = None if user.get("r") == "admin" else user.get("u")
    try:
        res = ctx.kb.reparse_document(doc_id, owner=owner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重新解析失败：{e}")
    ctx.audit.log(user.get("u", "anon"), "knowledge:reparse", doc_id)
    return {"ok": True, **res}


@router.get("/search")
async def search(q: str, top_k: int = 5, user=require_auth("knowledge:read")):
    ctx = get_ctx()
    owner = user.get("u")
    return {"query": q, "hits": ctx.kb.query(q, top_k, owner=owner)}


@router.get("/stats")
async def stats(user=require_auth("knowledge:read")):
    ctx = get_ctx()
    owner = user.get("u")
    return ctx.kb.stats(owner=owner)


@router.get("/graph")
async def graph(
    limit: int = Query(300, ge=10, le=2000, description="返回节点上限"),
    min_weight: int = Query(1, ge=1, description="边最小共现权重"),
    user=require_auth("knowledge:read"),
):
    """知识图谱（关键词共现网络）。

    普通用户仅见自己 + 共享文档贡献的子图；管理员（owner=None）可见全量。
    """
    ctx = get_ctx()
    if ctx.kb.graph is None:
        return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0}}
    owner = None if user.get("r") == "admin" else user.get("u")
    return ctx.kb.graph.get_graph(owner=owner, limit=limit, min_weight=min_weight)
