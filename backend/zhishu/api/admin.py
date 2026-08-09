"""智枢智能体 —— 管理路由（状态 / 审计）。"""
from __future__ import annotations

from fastapi import APIRouter, Body

from .auth import require_auth
from ..context import get_ctx
from ..core.tools import ToolRegistry

router = APIRouter(prefix="/api/v1", tags=["admin"])


@router.get("/admin/status")
async def status(user=require_auth("system:read")):
    ctx = get_ctx()
    return {
        "auth_enabled": ctx.cfg.security.enable_auth,
        "sm_enabled": ctx.cfg.security.enable_sm,
        "audit_enabled": ctx.cfg.security.enable_audit,
        "outbound_allowed": ctx.cfg.security.outbound_allow,
        "providers": [p.name for p in ctx.cfg.ordered_providers()],
        "default_model": ctx.cfg.default_model,
        "knowledge_base": ctx.kb.stats(),
        "tools": [t.name for t in ToolRegistry.all()],
    }


@router.get("/admin/audit")
async def audit(user=require_auth("audit:read"), limit: int = 100):
    ctx = get_ctx()
    return {"records": ctx.audit.recent(limit)}


@router.post("/admin/redact")
async def redact_test(req: dict = Body(...), user=require_auth("audit:read")):
    """脱敏自测：传入 {"text": "..."} 或 {"obj": {...}}，返回脱敏后结果（合规验证用）。"""
    ctx = get_ctx()
    if not ctx.cfg.security.enable_redact:
        return {"enabled": False, "result": req.get("text", "")}
    text = req.get("text")
    obj = req.get("obj")
    if obj is not None:
        return {"enabled": True, "result": ctx.redactor.redact_dict(obj)}
    return {"enabled": True, "result": ctx.redactor.redact(text or "")}


@router.get("/admin/redact/stats")
async def redact_stats_ep(user=require_auth("audit:read")):
    """脱敏命中统计（可观测性）：返回启用状态与累计调用/遮蔽计数。"""
    ctx = get_ctx()
    return {"enabled": ctx.cfg.security.enable_redact,
            "calls": ctx.redactor.stats.get("calls", 0),
            "masked": ctx.redactor.stats.get("masked", 0)}
