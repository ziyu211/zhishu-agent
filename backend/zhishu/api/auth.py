"""智枢智能体 —— 鉴权路由 + 依赖。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Header, Response
from pydantic import BaseModel

from ..context import get_ctx

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# /media 静态资源鉴权 Cookie 名（main.py 的 media 闸门中间件校验）。
# 浏览器 <img src="/media/..."> 不携带 Authorization 头，故在登录 /
# 会话校验时种一个 HttpOnly Cookie（作用域限 /media），实现同源受保护访问。
MEDIA_COOKIE = "zs_media_token"


def _set_media_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        MEDIA_COOKIE, quote(token, safe=""),
        path="/media", httponly=True, samesite="lax",
        max_age=86400 * 7,
    )


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
async def login(req: LoginReq, response: Response):
    ctx = get_ctx()
    session = ctx.auth.login(req.username, req.password)
    if not session:
        ctx.audit.log(req.username, "login_failed", "用户名或密码错误")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    ctx.audit.log(req.username, "login", f"登录成功（{session['role_label']}）")
    _set_media_cookie(response, session["token"])
    return session


def _extract_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    return authorization[7:] if authorization.startswith("Bearer ") else authorization


def require_auth(perm: str = "chat", skip_act_as: bool = False):
    def dependency(authorization: str | None = Header(None),
                   x_act_as: str | None = Header(None, alias="X-Act-As")):
        ctx = get_ctx()
        if not ctx.cfg.security.enable_auth:
            return {"u": "anonymous", "r": "admin"}
        token = _extract_token(authorization)
        data = ctx.auth.verify(token)
        if not data:
            raise HTTPException(status_code=401, detail="未登录或登录已过期")
        # 管理员「切换用户」：仅在当前用户为 admin 时生效；目标用户须存在。
        # 切换后 data 完全以目标用户身份运行（u=目标、r=目标角色），
        # 使所有隔离/归属/权限逻辑自动按目标用户生效；real_u 仅用于审计留痕。
        # skip_act_as=True 时（/me、/change-password、/logout）禁止代管穿透，
        # 避免身份初始化/自改密码被管理员 X-Act-As 误影响。
        if not skip_act_as and x_act_as and data.get("r") == "admin":
            row = ctx.users.get_by_name(x_act_as) if ctx.users else None
            if row is not None:
                tgt = dict(row)  # sqlite3.Row 无 .get()，须先转 dict
                data = {
                    **data,
                    "u": tgt.get("username") or x_act_as,
                    "r": tgt.get("role") or "user",
                    "real_u": data.get("u", "admin"),
                    "impersonating": True,
                }
        if not ctx.auth.can(data.get("r", ""), perm):
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        return data
    return Depends(dependency)


@router.get("/me")
async def me(response: Response,
            authorization: str | None = Header(None),
            user=require_auth("chat", skip_act_as=True)):
    from ..core.security import ROLE_LABELS, ROLES
    # 已在线会话（token 存 localStorage）打开页面时经 /me 刷新 media Cookie，
    # 使旧会话无需重新登录即可访问受保护的 /media 资源。
    token = _extract_token(authorization)
    if token:
        _set_media_cookie(response, token)
    role = user.get("r", "")
    return {
        "user": user.get("u", ""),
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "perms": ROLES.get(role, []),
    }


@router.post("/change-password")
async def change_password(req: ChangePwdReq,
                          user=require_auth("chat", skip_act_as=True)):
    ctx = get_ctx()
    username = user.get("u", "")
    if not ctx.users:
        raise HTTPException(status_code=400, detail="未启用多用户存储")
    u = ctx.users.verify_password(username, req.old_password)
    if not u:
        raise HTTPException(status_code=400, detail="原密码错误")
    try:
        ctx.users.set_password(u["id"], req.new_password)
        ctx.users.bump_epoch(u["id"])  # 改密后立即使旧令牌失效
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(username, "change_password", "修改自身密码")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response,
                user=require_auth("chat", skip_act_as=True)):
    """主动登出：吊销当前令牌（jti 加入 revoked 集合），并清除 media Cookie。"""
    ctx = get_ctx()
    ctx.auth.revoke_token(user.get("jti"))
    response.delete_cookie(MEDIA_COOKIE, path="/media")
    ctx.audit.log(user.get("u", ""), "logout", "主动登出")
    return {"ok": True}
