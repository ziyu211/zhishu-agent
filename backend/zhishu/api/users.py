"""智枢智能体 —— 多用户管理路由（仅管理员）。

提供用户的增删改查、重置密码、启用/停用。角色 RBAC 见 core/security.py。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .auth import require_auth
from ..context import get_ctx
from ..core.security import ROLES, ROLE_LABELS

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class CreateUserReq(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: str = ""


class UpdateUserReq(BaseModel):
    role: str | None = None
    status: str | None = None
    display_name: str | None = None


class ResetPwdReq(BaseModel):
    password: str


def _store():
    ctx = get_ctx()
    if not ctx.users:
        raise HTTPException(status_code=400, detail="未启用多用户存储")
    return ctx, ctx.users


@router.get("/roles")
async def roles(user=require_auth("users:read")):
    """供前端下拉：角色列表及其权限说明。"""
    return {
        "roles": [
            {"value": k, "label": ROLE_LABELS.get(k, k), "perms": v}
            for k, v in ROLES.items()
        ]
    }


@router.get("")
async def list_users(user=require_auth("users:read")):
    _, store = _store()
    return {"users": store.list()}


@router.post("")
async def create_user(req: CreateUserReq, user=require_auth("users:write")):
    ctx, store = _store()
    try:
        u = store.create(req.username, req.password, req.role, req.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("u", ""), "create_user", f"创建用户 {req.username}（{req.role}）")
    return u


@router.put("/{uid}")
async def update_user(uid: int, req: UpdateUserReq, user=require_auth("users:write")):
    ctx, store = _store()
    try:
        u = store.update(uid, role=req.role, status=req.status,
                         display_name=req.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("u", ""), "update_user", f"更新用户 #{uid}")
    return u


@router.post("/{uid}/password")
async def reset_password(uid: int, req: ResetPwdReq, user=require_auth("users:write")):
    ctx, store = _store()
    try:
        store.set_password(uid, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("u", ""), "reset_password", f"重置用户 #{uid} 密码")
    return {"ok": True}


@router.delete("/{uid}")
async def delete_user(uid: int, user=require_auth("users:write")):
    ctx, store = _store()
    try:
        store.delete(uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("u", ""), "delete_user", f"删除用户 #{uid}")
    return {"ok": True}
