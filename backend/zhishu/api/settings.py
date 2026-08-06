"""智枢智能体 —— 运行时设置（长期记忆 / 安全与网络等运行时开关的自助配置）。

设计：
  * GET  /api/v1/settings  —— 返回当前可被用户自助切换的运行时设置分组：
                              memory（长期记忆）、security（运行时安全开关）。
  * POST /api/v1/settings —— 应用补丁并持久化到 data_dir/config.override.json，
                             随后重建记忆管理器 / 同步审计脱敏器使开关立即生效。
  两个端点均要求 admin 权限（这些为全局结构性开关，影响全实例行为）。
"""
from __future__ import annotations

from fastapi import APIRouter, Body

from .auth import require_auth
from ..context import get_ctx

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _memory_view(ctx) -> dict:
    return {
        "vector_enabled": ctx.cfg.memory.vector_enabled,
        "vector_top_k": ctx.cfg.memory.vector_top_k,
    }


def _security_view(ctx) -> dict:
    s = ctx.cfg.security
    return {
         "allow_private_fetch": s.allow_private_fetch,
         "outbound_allow": s.outbound_allow,
         "allow_code_exec": s.allow_code_exec,
         "allow_shell": s.allow_shell,
         "shell_enforce_allowlist": s.shell_enforce_allowlist,
         "enable_audit": s.enable_audit,
         "enable_redact": s.enable_redact,
    }


# security 组可经前台切换的运行时字段（与 ctx._SECURITY_OVERRIDE_FIELDS 保持一致）
_SECURITY_FIELDS = (
    "allow_private_fetch", "outbound_allow", "allow_code_exec", "allow_shell",
    "shell_enforce_allowlist", "enable_audit", "enable_redact",
)


@router.get("")
async def get_settings(user=require_auth("admin")):
    ctx = get_ctx()
    return {
        "memory": _memory_view(ctx),
        "security": _security_view(ctx),
    }


@router.post("")
async def update_settings(
    body: dict = Body(default={}),
    user=require_auth("admin"),
):
    ctx = get_ctx()
    patch: dict = {}
    mem = body.get("memory") or {}
    if isinstance(mem.get("vector_enabled"), bool) or isinstance(mem.get("vector_top_k"), int):
        patch["memory"] = {
            "vector_enabled": mem.get("vector_enabled"),
            "vector_top_k": mem.get("vector_top_k"),
        }
    sec = body.get("security") or {}
    sec_part: dict = {}
    for k in _SECURITY_FIELDS:
        if isinstance(sec.get(k), bool):
            sec_part[k] = sec[k]
    if sec_part:
        patch["security"] = sec_part
    if not patch:
        return {
            "memory": _memory_view(ctx),
            "security": _security_view(ctx),
        }
    result = await ctx.apply_settings(patch)
    return result
