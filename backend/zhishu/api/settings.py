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
    m = ctx.cfg.memory
    return {
        "vector_enabled": m.vector_enabled,
        "vector_top_k": m.vector_top_k,
        "query_rewrite_enabled": m.query_rewrite_enabled,
        "extraction_enabled": m.extraction_enabled,
        "extraction_interval": m.extraction_interval,
        "extraction_model": m.extraction_model,
    }


def _security_view(ctx) -> dict:
    s = ctx.cfg.security
    return {
         "allow_private_fetch": s.allow_private_fetch,
         "outbound_allow": s.outbound_allow,
         "allow_code_exec": s.allow_code_exec,
         "code_exec_network_isolated": s.code_exec_network_isolated,
         "allow_shell": s.allow_shell,
         "shell_enforce_allowlist": s.shell_enforce_allowlist,
         "enable_audit": s.enable_audit,
         "enable_redact": s.enable_redact,
    }


# security 组可经前台切换的运行时字段（与 ctx._SECURITY_OVERRIDE_FIELDS 保持一致）
_SECURITY_FIELDS = (
    "allow_private_fetch", "outbound_allow", "allow_code_exec",
    "code_exec_network_isolated", "allow_shell",
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
    # 仅收集类型合法且确实提供的字段（避免把 None 误写进 override）
    mem_part: dict = {}
    if isinstance(mem.get("vector_enabled"), bool):
        mem_part["vector_enabled"] = mem["vector_enabled"]
    if isinstance(mem.get("vector_top_k"), int) and mem["vector_top_k"] > 0:
        mem_part["vector_top_k"] = mem["vector_top_k"]
    if isinstance(mem.get("query_rewrite_enabled"), bool):
        mem_part["query_rewrite_enabled"] = mem["query_rewrite_enabled"]
    if isinstance(mem.get("extraction_enabled"), bool):
        mem_part["extraction_enabled"] = mem["extraction_enabled"]
    if isinstance(mem.get("extraction_interval"), int) and mem["extraction_interval"] > 0:
        mem_part["extraction_interval"] = mem["extraction_interval"]
    if mem.get("extraction_model") is None or isinstance(mem.get("extraction_model"), str):
        mem_part["extraction_model"] = mem.get("extraction_model") or None
    if mem_part:
        patch["memory"] = mem_part
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
