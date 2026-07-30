"""智枢智能体 —— 子智能体（多 Agent 协作成员）管理路由。

与技能/插件/MCP 同一范式：目录即模块，data/agents/<name>/agent.json 存元信息，
启停状态存 data/agents_state.json（agents_disabled）。
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .auth import require_auth
from ..core.agents_runtime import (
    list_agents, read_agent_meta, write_agent_meta, delete_agent,
    sanitize_name, is_enabled, set_enabled, agent_owner,
)
from ..core.modules.runtime import can_view, can_edit, can_view_meta, can_edit_meta

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# 多用户隔离 helper（与 modules.py 同款范式）
#   * meta 无 owner（None/空）= 系统级共享：全员可见可用，仅 admin 可管理
#   * meta 有 owner          = 私有：仅 owner 与 admin 可见/可管理
#   * fail-closed：身份为空一律拒绝
# ---------------------------------------------------------------------------
def _is_admin(user: dict) -> bool:
    return (user.get("r") or "") == "admin"


def _username(user: dict) -> str:
    return (user.get("u") or "").strip()


def _role(user: dict) -> str:
    return (user.get("r") or "").strip()


def _guard_view(name: str, user: dict, label: str = "子智能体") -> dict:
    """存在 + 可见性校验；通过则返回 meta，否则 404（防枚举探测）。"""
    if not os.path.isdir(os.path.join(_agents_base(), name)):
        raise HTTPException(status_code=404, detail=f"未找到{label}：{name}")
    meta = read_agent_meta(name)
    if not can_view_meta(meta, _username(user), _is_admin(user), _role(user)):
        raise HTTPException(status_code=404, detail=f"未找到{label}：{name}")
    return meta


def _guard_edit(name: str, user: dict, label: str = "子智能体") -> dict:
    """存在 + 可写性校验；不可见→404，可见不可写（共享/角色共享项的非 owner）→403。"""
    meta = _guard_view(name, user, label)
    if not can_edit_meta(meta, _username(user), _is_admin(user)):
        raise HTTPException(status_code=403, detail=f"无权管理该{label}（共享项仅创建者可修改）")
    return meta


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class AgentBody(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    enabled: bool = True
    system_prompt: str = ""
    model: str | None = None
    tools: object = "all"          # "all" | "none" | list[str]
    max_steps: int | None = None
    shared: bool = False           # 显式共享：对他人可见可用
    share_with: list[str] = []     # 角色级共享：仅这些角色可见可用（shared=False 时生效）


class AgentUpdate(BaseModel):
    description: str | None = None
    version: str | None = None
    enabled: bool | None = None
    system_prompt: str | None = None
    model: str | None = None
    tools: object = None
    max_steps: int | None = None
    shared: Optional[bool] = None
    share_with: Optional[list[str]] = None


class _ToggleBody(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("")
async def get_agents(user=require_auth("agents:read")):
    return {"agents": list_agents(_username(user), _is_admin(user), _role(user))}


@router.get("/options")
async def get_agent_options(user=require_auth("agents:read")):
    """供聊天页选择器使用：返回已启用子智能体（name + description）。"""
    items = [
        {"name": a["name"], "description": a.get("description", "")}
        for a in list_agents(_username(user), _is_admin(user), _role(user)) if a.get("enabled")
    ]
    return {"agents": items}


@router.get("/{name}")
async def get_agent(name: str, user=require_auth("agents:read")):
    _guard_view(name, user)
    info = read_agent_meta(name)
    info["name"] = name
    info["owner"] = info.get("owner") or None
    info["shared"] = bool(info.get("shared"))
    info["share_with"] = list(info.get("share_with") or [])
    info["enabled"] = is_enabled(name)
    return info


@router.post("")
async def create_agent(body: AgentBody, user=require_auth("agents:write")):
    name = sanitize_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="子智能体名称非法")
    if os.path.isdir(os.path.join(_agents_base(), name)):
        raise HTTPException(status_code=409, detail=f"子智能体已存在：{name}")
    meta = {
        "name": name,
        "description": body.description,
        "version": body.version,
        "enabled": body.enabled,
        "system_prompt": body.system_prompt,
        "model": body.model,
        "tools": body.tools,
        "max_steps": body.max_steps,
        "owner": _username(user),
        "shared": bool(body.shared),
        "share_with": list(body.share_with or []),
    }
    write_agent_meta(name, meta)
    if not body.enabled:
        set_enabled(name, False)
    return {"ok": True, "name": name}


@router.put("/{name}")
async def update_agent(name: str, body: AgentUpdate, user=require_auth("agents:write")):
    _guard_edit(name, user)
    meta = read_agent_meta(name)
    for k in ("description", "version", "system_prompt", "model", "tools", "max_steps", "shared"):
        v = getattr(body, k)
        if v is not None:
            meta[k] = v
    if body.share_with is not None:
        meta["share_with"] = list(body.share_with or [])
    write_agent_meta(name, meta)
    return {"ok": True, "name": name}


@router.delete("/{name}")
async def remove_agent(name: str, user=require_auth("agents:write")):
    _guard_edit(name, user)
    delete_agent(name)
    return {"ok": True, "name": name}


@router.put("/{name}/toggle")
async def toggle_agent(name: str, body: _ToggleBody, user=require_auth("agents:write")):
    _guard_edit(name, user)
    set_enabled(name, body.enabled)
    return {"ok": True, "name": name, "enabled": body.enabled}


def _agents_base() -> str:
    from ..core.agents_runtime import agent_dir
    # agent_dir 需要 name，这里直接用其基目录逻辑
    from ..context import get_ctx
    return os.path.join(get_ctx().cfg.server.data_dir, "agents")
