"""智枢智能体 —— 定时任务路由。

安全：定时任务按 owner 隔离——非 admin 用户仅能查看/修改/删除/触发自己的任务；
创建时强制 owner 为当前用户（admin 可代他人创建）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .auth import require_auth
from ..context import get_ctx

router = APIRouter(prefix="/api/v1/cron", tags=["cron"])


class CronCreate(BaseModel):
    name: str
    schedule_type: str                 # interval | daily | cron
    schedule_config: dict             # {every,unit} | {hour,minute} | {expr}
    action: str                       # chat | shell
    payload: str                      # 提示词 或 命令
    model: str | None = None
    owner: str | None = None
    enabled: bool = True


class CronUpdate(BaseModel):
    name: str | None = None
    schedule_type: str | None = None
    schedule_config: dict | None = None
    action: str | None = None
    payload: str | None = None
    model: str | None = None
    owner: str | None = None
    enabled: bool | None = None


def _is_admin(user: dict) -> bool:
    return (user.get("r") or "") == "admin"


def _username(user: dict) -> str:
    return (user.get("u") or "").strip()


def _get_owned_job(jid: int, user: dict) -> dict:
    """取任务并校验归属：admin 放行；其他用户仅限 owner 为本人的任务。
    越权时返回 404（不暴露任务是否存在）。"""
    job = get_ctx().cron.get_job(jid)
    if not job:
        raise HTTPException(404, "任务不存在")
    if not _is_admin(user) and (job.get("owner") or "") != _username(user):
        raise HTTPException(404, "任务不存在")
    return job


@router.get("")
async def list_jobs(user=require_auth("cron:read")):
    jobs = get_ctx().cron.list_jobs()
    if _is_admin(user):
        return jobs
    me = _username(user)
    return [j for j in jobs if (j.get("owner") or "") == me]


@router.post("")
async def create_job(req: CronCreate, user=require_auth("cron:write")):
    if req.schedule_type not in ("interval", "daily", "cron"):
        raise HTTPException(400, "schedule_type 必须为 interval/daily/cron")
    if req.action not in ("chat", "shell"):
        raise HTTPException(400, "action 必须为 chat/shell")
    # 安全：shell 动作执行任意命令，仅限管理员/运维创建（防租户 RCE）
    if req.action == "shell" and (user.get("r") or "") not in ("admin", "operator"):
        raise HTTPException(403, "shell 动作执行任意命令，仅限管理员/运维创建")
    # 安全：非 admin 不得代他人创建任务，owner 强制为当前用户
    owner = req.owner if (_is_admin(user) and req.owner) else _username(user)
    jid = get_ctx().cron.create_job(
        req.name, req.schedule_type, req.schedule_config, req.action,
        req.payload, req.model, owner, 1 if req.enabled else 0)
    get_ctx().audit.log(user.get("u", ""), "cron_create",
                        f"创建定时任务 {req.name}({req.schedule_type}/{req.action})")
    return {"id": jid, "ok": True}


@router.put("/{jid}")
async def update_job(jid: int, req: CronUpdate, user=require_auth("cron:write")):
    _get_owned_job(jid, user)
    kw = {k: v for k, v in req.model_dump().items() if v is not None}
    # 安全：shell 动作执行任意命令，仅限管理员/运维（防租户 RCE）
    if kw.get("action") == "shell" and (user.get("r") or "") not in ("admin", "operator"):
        raise HTTPException(403, "shell 动作执行任意命令，仅限管理员/运维修改")
    # 安全：非 admin 不得转移任务归属
    if not _is_admin(user):
        kw.pop("owner", None)
    if not kw:
        raise HTTPException(400, "无可更新字段")
    get_ctx().cron.update_job(jid, **kw)
    # 若调度配置变化，立即重算下一触发时间
    if "schedule_type" in kw or "schedule_config" in kw:
        job = get_ctx().cron.get_job(jid)
        if job:
            from ..core.cron import next_run
            nr = next_run(job["schedule_type"], job["schedule_config"])
            get_ctx().cron._set_next(jid, nr)
    return {"ok": True}


@router.delete("/{jid}")
async def delete_job(jid: int, user=require_auth("cron:write")):
    _get_owned_job(jid, user)
    get_ctx().cron.delete_job(jid)
    get_ctx().audit.log(user.get("u", ""), "cron_delete", f"删除定时任务 #{jid}")
    return {"ok": True}


@router.put("/{jid}/toggle")
async def toggle_job(jid: int, enabled: bool, user=require_auth("cron:write")):
    _get_owned_job(jid, user)
    get_ctx().cron.set_enabled(jid, enabled)
    return {"ok": True}


@router.post("/{jid}/run")
async def run_now(jid: int, user=require_auth("cron:write")):
    """手动立即触发一次（异步执行，返回本次结果）。"""
    _get_owned_job(jid, user)
    out = await get_ctx().cron.run_now(jid)
    get_ctx().audit.log(user.get("u", ""), "cron_run", f"手动触发定时任务 #{jid}")
    return {"ok": True, "output": out}


@router.get("/{jid}/history")
async def job_history(jid: int, limit: int = 20, user=require_auth("cron:read")):
    _get_owned_job(jid, user)
    return get_ctx().cron.history(jid, limit)
