"""智枢智能体 —— 多用户对话路由（按 owner 隔离，管理员可看全部）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .auth import require_auth
from ..context import get_ctx

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


class CreateConvReq(BaseModel):
    title: str | None = None
    id: str | None = None  # 允许前端带着自己生成的 id 创建（保证与 chat 的 session 一致）


class UpdateConvReq(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    messages: list | None = None  # 前端在流式结束后回写完整消息


def _public_list(conv: dict) -> dict:
    """列表态：剔除大体积 messages，仅保留元信息。"""
    return {
        "id": conv["id"],
        "owner": conv["owner"],
        "title": conv["title"],
        "pinned": conv["pinned"],
        "message_count": conv["message_count"],
        "created_at": conv["created_at"],
        "updated_at": conv["updated_at"],
    }


@router.get("")
async def list_conversations(scope: str = "mine", user=require_auth("chat")):
    ctx = get_ctx()
    role = user.get("r", "")
    if scope == "all" and role == "admin":
        items = ctx.conversations.list(scope="all")
    else:
        items = ctx.conversations.list(owner=user.get("u", ""), scope="mine")
    return {"conversations": [_public_list(c) for c in items]}


@router.post("")
async def create_conversation(req: CreateConvReq, user=require_auth("chat")):
    ctx = get_ctx()
    owner = user.get("u", "")
    try:
        conv = ctx.conversations.create(owner, title=req.title or "新对话", cid=req.id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(owner, "create_conversation", f"创建对话 {conv['id']}")
    return conv


@router.get("/{cid}")
async def get_conversation(cid: str, user=require_auth("chat")):
    ctx = get_ctx()
    try:
        conv = ctx.conversations.get_for(cid, user.get("u", ""), user.get("r", ""))
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权访问该对话")
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


@router.put("/{cid}")
async def update_conversation(cid: str, req: UpdateConvReq, user=require_auth("chat")):
    ctx = get_ctx()
    try:
        conv = ctx.conversations.update(
            cid,
            user.get("u", ""),
            user.get("r", ""),
            title=req.title,
            pinned=req.pinned,
            messages=req.messages,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权修改该对话")
    except ValueError:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


@router.delete("/{cid}")
async def delete_conversation(cid: str, user=require_auth("chat")):
    ctx = get_ctx()
    try:
        ctx.conversations.delete(cid, user.get("u", ""), user.get("r", ""))
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权删除该对话")
    except ValueError:
        raise HTTPException(status_code=404, detail="对话不存在")
    ctx.audit.log(user.get("u", ""), "delete_conversation", f"删除对话 {cid}")
    return {"ok": True}
