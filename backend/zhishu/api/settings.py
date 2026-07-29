"""智枢智能体 —— 运行时设置（长期记忆等 opt-in 特性的自助开关）。

设计：
  * GET  /api/v1/settings  —— 返回当前可被用户自助切换的运行时设置（当前为长期记忆）。
  * POST /api/v1/settings —— 应用补丁并持久化到 data_dir/config.override.json，
                             随后重建记忆管理器使开关立即生效。
  两个端点均要求 admin 权限（长期记忆为全局结构性开关，影响所有用户的对话召回）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body

from .auth import require_auth
from ..context import get_ctx

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _memory_view(ctx) -> dict:
    return {
        "vector_enabled": ctx.cfg.memory.vector_enabled,
        "vector_top_k": ctx.cfg.memory.vector_top_k,
    }


@router.get("")
async def get_settings(user=require_auth("admin")):
    ctx = get_ctx()
    return {"memory": _memory_view(ctx)}


@router.post("")
async def update_settings(
    body: dict = Body(default={}),
    user=require_auth("admin"),
):
    ctx = get_ctx()
    mem = body.get("memory") or {}
    patch: dict = {"memory": {}}
    if isinstance(mem.get("vector_enabled"), bool):
        patch["memory"]["vector_enabled"] = mem["vector_enabled"]
    if isinstance(mem.get("vector_top_k"), int) and mem["vector_top_k"] > 0:
        patch["memory"]["vector_top_k"] = mem["vector_top_k"]
    if not patch["memory"]:
        return {"memory": _memory_view(ctx)}
    result = await ctx.apply_settings(patch)
    return {"memory": result}
