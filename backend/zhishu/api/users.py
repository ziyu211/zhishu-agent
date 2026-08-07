"""智枢智能体 —— 多用户管理路由（仅管理员）。

提供用户的增删改查、重置密码、启用/停用。角色 RBAC 见 core/security.py。
"""
from __future__ import annotations

import os
import shutil

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
    # 删除前先取用户名（删除后无法再查），用于级联清理其全部归属数据。
    target = store.get(uid)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    username = target.get("username", "")
    try:
        store.delete(uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 级联清理：删除用户时一并清除其孤儿数据，避免同名重建后继承前任全部内容
    # （对话、记忆、凭证、定时任务、知识库、本地文件目录）。
    try:
        ctx.conversations.delete_by_owner(username)
    except Exception:
        pass
    try:
        ctx.providers.delete_by_owner(username)
    except Exception:
        pass
    try:
        ctx.memory.clear_session_prefix(f"{username}:")
    except Exception:
        pass
    try:
        for j in ctx.cron.list_jobs():
            if isinstance(j, dict) and j.get("owner") == username:
                ctx.cron.delete_job(j["id"])
    except Exception:
        pass
    # 级联清理：被删用户拥有的子智能体（目录 + agents_state.json 禁用残留）
    try:
        from ..core.agents_runtime import delete_agents_by_owner
        delete_agents_by_owner(username)
    except Exception:
        pass
    try:
        docs = ctx.kb.list_documents(owner=username, limit=10000)
        for d in docs:
            try:
                ctx.kb.delete_document(d.get("doc_id"), owner=username)
            except Exception:
                pass
    except Exception:
        pass
    for d in (os.path.join(ctx.cfg.server.data_dir, "memory", username),
              os.path.join(ctx.cfg.server.data_dir, ctx.cfg.media.store_dir, "attachments", username),
              os.path.join(ctx.cfg.server.data_dir, ctx.cfg.media.store_dir, username)):
        try:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    ctx.audit.log(user.get("u", ""), "delete_user", f"删除用户 {username}（已级联清理归属数据）")
    return {"ok": True}
