"""智枢智能体 —— 鉴权路由 + 依赖。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

from ..context import get_ctx

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str


@router.get("/status")
async def auth_status():
    """登录页用于判断登录方式与是否已初始化用户。"""
    ctx = get_ctx()
    return {
        "auth_enabled": ctx.cfg.security.enable_auth,
        "password_login": True,
        "user_count": ctx.users.count() if ctx.users else 0,
    }


@router.post("/login")
async def login(req: LoginReq):
    ctx = get_ctx()
    session = ctx.auth.login(req.username, req.password)
    if not session:
        ctx.audit.log(req.username, "login_failed", "用户名或密码错误")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    ctx.audit.log(req.username, "login", f"登录成功（{session['role_label']}）")
    return session


def _extract_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    return authorization[7:] if authorization.startswith("Bearer ") else authorization


def _current(authorization: str | None) -> dict:
    ctx = get_ctx()
    if not ctx.cfg.security.enable_auth:
        return {"u": "anonymous", "r": "admin"}
    data = ctx.auth.verify(_extract_token(authorization))
    if not data:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return data


@router.get("/me")
async def me(authorization: str | None = Header(None)):
    from ..core.security import ROLE_LABELS
    data = _current(authorization)
    role = data.get("r", "")
    return {
        "user": data.get("u", ""),
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
    }


@router.post("/change-password")
async def change_password(req: ChangePwdReq, authorization: str | None = Header(None)):
    ctx = get_ctx()
    data = _current(authorization)
    username = data.get("u", "")
    if not ctx.users:
        raise HTTPException(status_code=400, detail="未启用多用户存储")
    u = ctx.users.verify_password(username, req.old_password)
    if not u:
        raise HTTPException(status_code=400, detail="原密码错误")
    try:
        ctx.users.set_password(u["id"], req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(username, "change_password", "修改自身密码")
    return {"ok": True}


def require_auth(perm: str = "chat"):
    def dependency(authorization: str | None = Header(None)):
        ctx = get_ctx()
        if not ctx.cfg.security.enable_auth:
            return {"u": "anonymous", "r": "admin"}
        token = _extract_token(authorization)
        data = ctx.auth.verify(token)
        if not data:
            raise HTTPException(status_code=401, detail="未登录或登录已过期")
        if not ctx.auth.can(data.get("r", ""), perm):
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        return data
    return Depends(dependency)
