"""智枢智能体 —— 对话路由（SSE 流式 + 附件解析）。"""
from __future__ import annotations

import io
import json
import os
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from .auth import require_auth
from ..context import get_ctx
from ..core.agent import Agent, build_context_engine
from ..core import parsers
from ..core.rag import read_file_text
from ..core.concurrency import get_limiter, ConcurrencyLimitError

router = APIRouter(prefix="/api/v1", tags=["chat"])

# 图片扩展名（进入对话框后作为视觉参考；系统不内置 OCR）
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".svg",
}

# 返回给前端的解析文本上限（避免超大文档撑爆响应）
_MAX_TEXT = 20000


def _attachment_url(rel_path: str) -> str:
    """media 挂载在 data_dir/store_dir 下，rel_path 为其相对路径。"""
    return "/media/" + rel_path.replace("\\", "/").lstrip("/")


def _save_upload(raw: bytes, filename: str, owner: str | None = None) -> tuple[str, str]:
    """保存到 media/attachments/<owner>/<uid>/<filename>，返回 (相对 media 的路径, 绝对路径)。

    owner 段用于越权防护：解析/查看他人附件时按归属校验。
    """
    ctx = get_ctx()
    base = os.path.join(ctx.cfg.server.data_dir, ctx.cfg.media.store_dir, "attachments")
    owner_dir = owner or "_anon"
    uid_dir = uuid.uuid4().hex[:12]
    target_dir = os.path.join(base, owner_dir, uid_dir)
    os.makedirs(target_dir, exist_ok=True)
    safe_name = os.path.basename(filename) or "file"
    abs_path = os.path.abspath(os.path.join(target_dir, safe_name))
    with open(abs_path, "wb") as f:
        f.write(raw)
    rel = os.path.join("attachments", owner_dir, uid_dir, safe_name)
    return rel, abs_path


def _need_plugin_payload(name: str | None) -> dict | None:
    if not name:
        return None
    info = parsers.PARSE_PLUGINS.get(name)
    if not info:
        return {"name": name, "description": "", "version": ""}
    return {
        "name": info["name"],
        "description": info["description"],
        "version": info.get("version", ""),
    }


from ..core.upload import read_upload_limited

@router.post("/chat/attach")
async def chat_attach(
    file: UploadFile = File(...),
    user=require_auth("chat"),
):
    """附件进入对话框：**仅落盘，不解析**（对标 hermes「上传只落盘」）。

    解析完全按需：对话中由 read_file 工具读取磁盘上的原始文件进行提取。
    返回 stored_path（绝对路径，供 read_file 工具读取）、url（前端展示/下载）、
    file_id、status="stored"，以及图片的视觉能力标记。
    """
    ctx = get_ctx()
    raw = await read_upload_limited(file)
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    filename = file.filename or "未命名文件"
    ext = os.path.splitext(filename)[1].lower()
    owner = user.get("u")
    rel, abs_path = _save_upload(raw, filename, owner=owner)

    base = {
        "attachment_id": os.path.basename(os.path.dirname(abs_path)),
        "file_id": os.path.basename(os.path.dirname(abs_path)),
        "title": filename,
        "file_type": ext.lstrip(".").upper() or "FILE",
        "is_image": ext in _IMAGE_EXTS,
        "url": _attachment_url(rel),
        "stored_path": abs_path,
        "status": "stored",          # 仅落盘，待 agent 按需 read_file
        "parsed": False,
        "text": None,
        "text_total": None,
        "doc_id": None,
        "vision_available": False,
        "needs_plugin": None,
        "parse_error": None,
    }
    if base["is_image"]:
        # 图片统一作为视觉参考进入对话（系统不内置 OCR，无法提取图片内文字）
        base["vision_available"] = True
    return base


def _parse_stored(ctx, abs_path: str, filename: str, owner: str | None) -> dict:
    """文档/文本：零依赖提取（read_file_text 内部优先 stdlib）并入库（在线程池内）。
    返回需合并进前端的字段；解析失败给出 parse_error。"""
    out: dict = {"parsed": False, "text": None, "text_total": None,
                 "doc_id": None, "parse_error": None, "needs_plugin": None}
    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
        media_root = os.path.normpath(os.path.abspath(
            os.path.join(ctx.cfg.server.data_dir, ctx.cfg.media.store_dir)))
        text, ftype = read_file_text(filename, raw, media_root, owner)
    except Exception as e:  # noqa: BLE001
        out["parse_error"] = f"解析失败: {e}"
        return out
    if not text.strip():
        out["parse_error"] = "未提取到可解析文本（可能是图片型文档，请转换为文本型文档）"
        return out
    res = ctx.kb.ingest_text(
        text, owner=owner, title=filename, file_type=ftype, source=filename,
    )
    out["parsed"] = True
    out["text"] = text[:_MAX_TEXT]
    out["text_total"] = len(text)
    out["doc_id"] = res.get("doc_id")
    out["file_type"] = ftype
    return out


@router.post("/chat/parse")
async def chat_parse(
    path: str = Form(...),
    user=require_auth("chat"),
):
    """按 stored_path（附件绝对路径或 /media/ URL）解析**已落盘**的附件：

    - 图片：系统不内置 OCR，无法提取图片内文字，统一返回提示（请作为视觉参考使用）；
    - 文档/文本：read_file_text 零依赖提取（docx/xlsx 标准库、pdf/csv/txt 等）。

    解析结果入库便于后续检索，返回 parsed/text/text_total/doc_id/needs_plugin/
    parse_error，供前端**直接**把内容喂给模型（无需模型自行调用 read_file）。
    """
    ctx = get_ctx()
    p = (path or "").strip()
    media_root = os.path.normpath(os.path.abspath(
        os.path.join(ctx.cfg.server.data_dir, ctx.cfg.media.store_dir)))
    if p.startswith("/media/"):
        p = p[len("/media/"):]
        abs_path = os.path.normpath(os.path.join(media_root, p))
    else:
        # chat/attach 返回的 stored_path（绝对路径），必须位于 media 托管目录内
        abs_path = os.path.normpath(os.path.abspath(p))
    # 安全：白名单校验 —— 只允许解析 media 目录内的已落盘附件，
    # 拒绝任意绝对路径（防止读取 providers.json、users.json、他人记忆、源码等）
    if not (abs_path == media_root or abs_path.startswith(media_root + os.sep)):
        raise HTTPException(status_code=403, detail="非法附件路径")
    if not abs_path or not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="附件不存在")
    # 越权防护：附件解析会把内容入库到本人知识库，必须确认附件归属本人
    rel = os.path.relpath(abs_path, media_root)
    rel_parts = rel.split(os.sep)
    if rel_parts and rel_parts[0] == "attachments" and len(rel_parts) >= 3:
        owner_seg = rel_parts[1]
        if owner_seg not in ("_anon",) and owner_seg != (user.get("u") or "") \
                and (user.get("r") or "") != "admin":
            raise HTTPException(status_code=403, detail="无权解析他人附件")
    owner = user.get("u")
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in _IMAGE_EXTS:
        # 图片：系统不内置 OCR，无法提取文字，仅作为视觉参考
        return {"parsed": False, "text": None, "text_total": None, "doc_id": None,
                "needs_plugin": None,
                "parse_error": "图片暂不支持文字提取（系统不内置 OCR），请作为视觉参考使用。",
                "status": "error"}
    # 文档/文本：零依赖提取
    out = await run_in_threadpool(_parse_stored, ctx, abs_path, os.path.basename(abs_path), owner)
    out["status"] = "done" if out.get("parsed") else ("error" if out.get("parse_error") else "done")
    return out


class ChatReq(BaseModel):
    message: str
    session: str = "default"
    model: str | None = None
    # 参考图（图生图 / 图生视频）：公网 URL 或 Data URI Base64（data:image/png;base64,...）
    image: str | None = None
    # 指定直接对话的子智能体（管理后台「智能体」模块中已启用的成员）。
    # 留空表示由主管智能体应答（主管可在需要时自动委派 delegate_to_agent）。
    agent: str | None = None
    # 随消息发送的附件（chat/attach 已落盘）。每项形如：
    #   {title, file_type, is_image, url, stored_path, vision_available}
    # 后端按 hermes 方式处置：图片作为视觉参考（native vision part），
    # 扫描件 PDF 渲染为图片喂视觉模型（不 OCR），文本文档注入路径提示让 Agent 自取。
    attachments: list[dict] | None = None


@router.post("/chat")
async def chat(req: ChatReq, user=require_auth("chat")):
    ctx = get_ctx()
    username = user.get("u", "")
    role = user.get("r", "")

    # ---- 对话归属校验 / 自动创建（防越权读取他人对话）----
    conv = None
    try:
        conv = ctx.conversations.get_for(req.session, username, role)
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权访问该对话")
    if conv is None:
        # 不存在则按当前用户创建（保证 owner 隔离）
        conv = ctx.conversations.create(username, title="新对话", cid=req.session)
    owner = conv["owner"]
    is_admin = role == "admin"
    # 记忆命名空间隔离：owner:session，即使 session id 相同也不会串号
    memory_session = f"{owner}:{req.session}"

    agent = Agent(
        ctx.cfg, ctx.llm, ctx.kb, ctx.memory, ctx.tool_ctx, media=ctx.media,
        context_engine=build_context_engine(ctx.cfg, ctx.llm),
        memory_manager=ctx.memory_manager,
    )

    # 指定子智能体时做存在/启用/归属校验（避免越权或拼写错误）
    target = None
    if req.agent:
        from ..core.agents_runtime import get_agent_meta, agent_owner
        from ..core.modules.runtime import can_view
        meta = get_agent_meta(req.agent)
        # 多用户隔离：他人私有子智能体视同不存在（防枚举探测）
        if not meta or not can_view(meta.get("owner") or None, username, is_admin,
                                    bool(meta.get("shared")), meta.get("share_with") or None, role):
            raise HTTPException(status_code=404, detail=f"未找到子智能体：{req.agent}")
        if not meta.get("enabled"):
            raise HTTPException(status_code=403, detail=f"子智能体已停用：{req.agent}")
        target = req.agent

    async def event_gen():
        # 企业级并发/配额限流（P0-1）：在整段对话流生命周期内持有信号量，真实反映
        # 「活跃会话」占用；被拒时返回友好错误而非占用资源。子智能体委派属于嵌套执行，
        # 不再二次占额（避免死锁）。
        limiter = get_limiter()
        acquired = False
        try:
            await limiter.acquire(owner)
            acquired = True
        except ConcurrencyLimitError as e:
            yield {"data": json.dumps(
                {"type": "error", "message": str(e)}, ensure_ascii=False)}
            return
        try:
            async for ev in agent.run(req.message, memory_session, req.model,
                                      image=req.image, owner=owner, agent_name=target,
                                      attachments=req.attachments, is_admin=is_admin,
                                      user_role=role):
                # SSE data 行；前端 EventSource.onmessage 解析 JSON
                yield {"data": json.dumps(ev, ensure_ascii=False)}
        except Exception as e:  # noqa: BLE001
            # 兜底安全网：agent.run 内任何未预期异常（图片/附件处理、工具、模型调用等）
            # 若冲出生成器，SSE 流会被掐断，浏览器侧表现为「network error」。这里统一
            # 转为清晰的错误事件，保证流正常结束，前端友好展示而非断流。
            import traceback as _tb
            _tb.print_exc()
            yield {"data": json.dumps(
                {"type": "error",
                 "message": f"对话处理出错：{e}。请稍后重试，或检查模型/附件配置。"},
                ensure_ascii=False)}
        finally:
            if acquired:
                await limiter.release(owner)
        ctx.audit.log(user.get("real_u", username), "chat", req.message[:200], f"agent={target or 'supervisor'}")

    return EventSourceResponse(
        event_gen(),
        # 周期性 SSE 心跳（注释行 `: ping`），即便后端长时间「思考 / 调工具」没有业务事件，
        # 也能维持连接，避免浏览器或中间代理（nginx/CDN/ingress）因空闲超时而掐断流，
        # 表现为「没结果 / network error」。前端解析器会忽略非 data: 行，不受影响。
        ping=5,
    )
