"""智枢智能体 —— 技能 / 插件 / MCP / 记忆 模块路由（完整 CRUD + 运行时接入）。

  * skills / plugins / mcp 均以"目录即模块"管理（data/<sub>/<name>/ 下的 json 元信息）。
  * 启用/停用状态写入 data/modules_state.json 的 disabled 列表（与文件内 enabled 字段取交集）。
  * memory 直接读写 data/MEMORY.md、USER.md、SOUL.md 三份长期记忆文件。
  * MCP 额外提供 详情/连接/调用/刷新 接口，使服务器工具可立即被 Agent 使用。
"""
from __future__ import annotations

import io
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("zhishu.modules")

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from .auth import require_auth
from ..context import get_ctx
from ..core.modules import (
    load_state,
    save_state,
    read_meta,
    write_meta,
    delete_module,
    sanitize_name,
    module_dir,
    register_plugin_tools,
    DISABLED_KEY,
)
from ..core.modules.runtime import can_view, can_edit, can_view_meta, can_edit_meta, filter_tool_specs
from ..core.modules.skills_io import import_archive, export_skills


router = APIRouter(prefix="/api/v1", tags=["modules"])


# ---------------------------------------------------------------------------
# 多用户隔离（与 api/cron.py 同一范式）：
#   * meta 无 owner = 系统级共享：全员可见可用，仅 admin 可管理
#   * meta 有 owner = 私有：仅 owner + admin 可见/可管理
#   * 越权访问他人私有模块 → 404（视同不存在，防枚举探测）
# ---------------------------------------------------------------------------
def _is_admin(user: dict) -> bool:
    return (user.get("r") or "") == "admin"


def _username(user: dict) -> str:
    return (user.get("u") or "").strip()


def _role(user: dict) -> str:
    return (user.get("r") or "").strip()


def _guard_view(sub: str, name: str, user: dict, label: str) -> dict:
    """存在 + 可见性校验；通过则返回 meta，否则 404。"""
    if not os.path.isdir(module_dir(sub, name)):
        raise HTTPException(status_code=404, detail=f"未找到{label}：{name}")
    meta = read_meta(sub, name)
    if not can_view_meta(meta, _username(user), _is_admin(user), _role(user)):
        raise HTTPException(status_code=404, detail=f"未找到{label}：{name}")
    return meta


def _guard_edit(sub: str, name: str, user: dict, label: str) -> dict:
    """存在 + 可写性校验；不可见→404，可见不可写（共享/角色共享项的非 owner）→403。"""
    meta = _guard_view(sub, name, user, label)
    if not can_edit_meta(meta, _username(user), _is_admin(user)):
        raise HTTPException(status_code=403, detail=f"无权管理该{label}（共享项仅创建者可修改）")
    return meta


# ---------------------------------------------------------------------------
# 通用：列表 / 启停
# ---------------------------------------------------------------------------
_ENV_MASK = "******"


def _mask_env(env: Optional[dict]) -> dict:
    """脱敏 MCP env：仅回传键名与掩码，绝不把密钥明文返回给前端。
    前端保存时若某键的值仍为掩码，则视为未修改、保留原值（见 update_mcp）。"""
    return {k: (_ENV_MASK if str(v or "") else "") for k, v in (env or {}).items()}


def _list_modules(sub: str, user: Optional[dict] = None) -> list:
    from ..core.modules import module_dir as _md
    base = _md(sub, "")
    state = load_state()
    disabled = set(state.get(DISABLED_KEY[sub], []))
    out: list = []
    if not os.path.isdir(base):
        return out
    uname = _username(user) if user else ""
    admin = _is_admin(user) if user else False
    urole = _role(user) if user else ""
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d) or name in disabled or name.startswith("."):
            continue
        info = read_meta(sub, name)
        if info.get("enabled") is False:
            continue
        # 多用户隔离：仅返回「共享 + 本人 + 角色命中」；admin 全量（含 owner 归属信息）
        owner_val = info.get("owner") or None
        if not can_view_meta(info, uname, admin, urole):
            continue
        item: dict[str, Any] = {
            "name": name,
            "description": info.get("description", ""),
            "version": info.get("version", ""),
            "enabled": name not in disabled,
            "owner": owner_val,
            "shared": bool(info.get("shared")),
            "share_with": list(info.get("share_with") or []),
        }
        if sub == "plugins":
            item["tool_count"] = len(info.get("tools") or [])
        if sub == "skills":
            # 技能使用统计（对标 Hermes skill_usage）：read_skill 命中自增
            item["use_count"] = int(info.get("use_count") or 0)
            item["last_used"] = info.get("last_used")
        if sub == "mcp":
            item["command"] = info.get("command", "")
            item["args"] = info.get("args", [])
            item["env"] = _mask_env(info.get("env", {}))
        out.append(item)
    return out


def _toggle(sub: str, name: str, enabled: bool) -> dict:
    if not os.path.isdir(module_dir(sub, name)):
        raise HTTPException(status_code=404, detail=f"未找到模块：{name}")
    state = load_state()
    disabled = set(state.get(DISABLED_KEY[sub], []))
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    state[DISABLED_KEY[sub]] = sorted(disabled)
    save_state(state)
    return {"name": name, "enabled": enabled}


def _sync_plugins():
    """插件增删改/启停后即时把已启用插件的工具注册进 ToolRegistry，
    使模块改动立即生效，无需手动『刷新』或重启。"""
    try:
        register_plugin_tools()
    except Exception as e:
        # 注册异常已被 register_plugin_tools 内部按插件隔离，正常不应触达此处；
        # 若触达（如 ToolRegistry 自身异常）则记录日志而非静默吞掉，避免「假成功」不可观测。
        logger.error("[modules] 插件工具注册异常（已隔离，不影响其余功能）：%s", e)


class _ToggleBody(BaseModel):
    enabled: bool = True


# ---------------------------------------------------------------------------
# 技能 Skills（content 为注入 Agent 的指令）
# ---------------------------------------------------------------------------
class SkillBody(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    content: str = ""
    enabled: bool = True
    shared: bool = False          # 显式共享：对他人可见可用
    share_with: list[str] = []    # 角色级共享：仅这些角色可见可用（shared=False 时生效）


class SkillUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    content: Optional[str] = None
    enabled: Optional[bool] = None
    shared: Optional[bool] = None
    share_with: Optional[list[str]] = None


@router.get("/skills")
async def list_skills(user=require_auth("modules:read")):
    return {"skills": _list_modules("skills", user)}


@router.post("/skills/import")
async def import_skills(file: UploadFile = File(...), user=require_auth("modules:write")):
    """从外部智能体（Hermes / OpenClaw / 智枢原生 / 通用 Markdown 压缩包）批量导入技能。

    上传 .zip / .tgz / .tar.gz，自动嗅探格式并转换为智枢技能目录。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    fn = (file.filename or "").lower()
    fmt = "tgz" if (fn.endswith(".tgz") or fn.endswith(".tar.gz")) else "zip"
    try:
        # 默认私有：导入产物归属导入者（含 admin 自身）；archived 内已带 owner/shared 的保留其原值。
        owner = _username(user)
        res = import_archive(data, fmt, get_ctx().cfg.server.data_dir, owner=owner)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"导入失败：{e}")
    return {"ok": True, **res}


@router.get("/skills/export")
async def export_skills_all(user=require_auth("modules:read")):
    """导出技能为 zip（智枢原生格式，兼容 Hermes 的 SKILL.md 约定）。

    多用户隔离：admin 导出全部；普通用户仅导出「共享 + 本人」技能。"""
    names = None
    if not _is_admin(user):
        names = [it["name"] for it in _list_modules("skills", user)]
    data = export_skills(get_ctx().cfg.server.data_dir, names=names)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=zhishu-skills.zip"},
    )


@router.get("/skills/{name}/export")
async def export_skill_one(name: str, user=require_auth("modules:read")):
    """导出单个技能为 zip。"""
    _guard_view("skills", name, user, "技能")
    data = export_skills(get_ctx().cfg.server.data_dir, names=[name])
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=zhishu-skill-{name}.zip"},
    )


@router.get("/skills/{name}")
async def get_skill(name: str, user=require_auth("modules:read")):
    info = _guard_view("skills", name, user, "技能")
    info["name"] = name
    info["owner"] = info.get("owner") or None
    info["shared"] = bool(info.get("shared"))
    info["share_with"] = list(info.get("share_with") or [])
    info["enabled"] = name not in set(load_state().get("skills_disabled", []))
    return info


@router.post("/skills")
async def create_skill(body: SkillBody, user=require_auth("modules:write")):
    name = sanitize_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="技能名称非法")
    if os.path.isdir(module_dir("skills", name)):
        raise HTTPException(status_code=409, detail=f"技能已存在：{name}")
    # 默认私有：归属创建者（含 admin 自身）；shared=True 或 share_with 才对他人可见可用。
    meta = {
        "name": name,
        "description": body.description,
        "version": body.version,
        "content": body.content,
        "enabled": body.enabled,
        "owner": _username(user),
        "shared": bool(body.shared),
        "share_with": list(body.share_with or []),
        "created_by": "user",
    }
    write_meta("skills", name, meta)
    return {"ok": True, "name": name}


@router.put("/skills/{name}")
async def update_skill(name: str, body: SkillUpdate, user=require_auth("modules:write")):
    meta = _guard_edit("skills", name, user, "技能")
    for k in ("description", "version", "content", "enabled", "shared"):
        v = getattr(body, k)
        if v is not None:
            meta[k] = v
    if body.share_with is not None:
        meta["share_with"] = list(body.share_with or [])
    write_meta("skills", name, meta)
    return {"ok": True, "name": name}


@router.delete("/skills/{name}")
async def remove_skill(name: str, user=require_auth("modules:write")):
    _guard_edit("skills", name, user, "技能")
    delete_module("skills", name)
    return {"ok": True, "name": name}


@router.get("/skills/learning-graph")
async def learning_graph(user=require_auth("modules:read")):
    """学习血缘图谱（对标 Hermes learning_graph）：技能节点（含 created_by / 血缘 /
    使用统计）+ 长期记忆节点 + 关联边。多用户隔离：非 admin 只看自己，admin 看全局。"""
    from ..core.memory.learning_graph import build_learning_graph
    cfg = get_ctx().cfg
    owner = None if _is_admin(user) else _username(user)
    graph = build_learning_graph(cfg.server.data_dir, owner=owner, is_admin=_is_admin(user))
    return graph


@router.post("/skills/curator/run")
async def run_curator(user=require_auth("modules:write")):
    """立即跑一次确定性剪枝（可选 dry_run 仅统计不归档）。返回各状态变更计数。"""
    from ..core.memory.curator import apply_curator
    cfg = get_ctx().cfg
    counts = apply_curator(cfg.agent, cfg.server.data_dir, owner=_username(user))
    return {"ok": True, "counts": counts}


@router.get("/skills/curator/state")
async def curator_state(user=require_auth("modules:read")):
    """查看技能库管家最近一次巡检状态。"""
    from ..core.memory.curator import load_state
    return load_state(get_ctx().cfg.server.data_dir)


@router.put("/skills/{name}/toggle")
async def toggle_skill(name: str, body: _ToggleBody, user=require_auth("modules:write")):
    _guard_edit("skills", name, user, "技能")
    return _toggle("skills", name, body.enabled)


# ---------------------------------------------------------------------------
# 插件 Plugins（tools 为注册到 Agent 的自定义工具）
# ---------------------------------------------------------------------------
class PluginTool(BaseModel):
    name: str
    description: str = ""
    type: str = "shell"          # shell | http
    command: str = ""            # shell: 可执行文件；http: 忽略
    args: list = []              # shell: 参数模板（支持 {{arg}}）
    command_is_template: bool = False
    url: str = ""                # http: 目标地址
    method: str = "POST"
    headers: dict = {}
    body_template: str = ""
    parameters: list = []        # [{name, type, description, required}]


class PluginBody(BaseModel):
    name: str
    description: str = ""
    version: str = "0.1"
    enabled: bool = True
    tools: list = []
    shared: bool = False          # 显式共享：对他人可见可用
    share_with: list[str] = []    # 角色级共享：仅这些角色可见可用（shared=False 时生效）


class PluginUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    enabled: Optional[bool] = None
    tools: Optional[list] = None
    shared: Optional[bool] = None
    share_with: Optional[list[str]] = None


@router.get("/plugins")
async def list_plugins(user=require_auth("modules:read")):
    return {"plugins": _list_modules("plugins", user)}


def _mask_plugin_tools(tools: Optional[list]) -> list:
    """脱敏插件工具定义中的 http headers（可能含 Authorization/API Key）。"""
    out = []
    for t in tools or []:
        if isinstance(t, dict) and t.get("headers"):
            t = dict(t)
            t["headers"] = _mask_env(t.get("headers"))
        out.append(t)
    return out


def _restore_plugin_tools(new_tools: list, old_tools: Optional[list]) -> list:
    """保存插件时：headers 中值为掩码的键视为未修改，从旧配置恢复明文。"""
    old_by_name = {t.get("name"): t for t in (old_tools or []) if isinstance(t, dict)}
    out = []
    for t in new_tools or []:
        if isinstance(t, dict) and t.get("headers"):
            t = dict(t)
            old_h = (old_by_name.get(t.get("name")) or {}).get("headers") or {}
            t["headers"] = {
                k: (old_h.get(k, "") if str(v) == _ENV_MASK else v)
                for k, v in dict(t["headers"]).items()
            }
        out.append(t)
    return out


@router.get("/plugins/{name}")
async def get_plugin(name: str, user=require_auth("modules:read")):
    info = _guard_view("plugins", name, user, "插件")
    info["name"] = name
    info["owner"] = info.get("owner") or None
    info["shared"] = bool(info.get("shared"))
    info["share_with"] = list(info.get("share_with") or [])
    # 安全：http 工具 headers 脱敏后再回传
    info["tools"] = _mask_plugin_tools(info.get("tools"))
    info["enabled"] = name not in set(load_state().get("plugins_disabled", []))
    return info


@router.post("/plugins")
async def create_plugin(body: PluginBody, user=require_auth("modules:write")):
    name = sanitize_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="插件名称非法")
    if os.path.isdir(module_dir("plugins", name)):
        raise HTTPException(status_code=409, detail=f"插件已存在：{name}")
    meta = {
        "name": name,
        "description": body.description,
        "version": body.version,
        "enabled": body.enabled,
        "tools": body.tools,
        "owner": _username(user),
        "shared": bool(body.shared),
        "share_with": list(body.share_with or []),
    }
    write_meta("plugins", name, meta)
    _sync_plugins()
    return {"ok": True, "name": name}


@router.put("/plugins/{name}")
async def update_plugin(name: str, body: PluginUpdate, user=require_auth("modules:write")):
    meta = _guard_edit("plugins", name, user, "插件")
    for k in ("description", "version", "enabled", "shared"):
        v = getattr(body, k)
        if v is not None:
            meta[k] = v
    if body.share_with is not None:
        meta["share_with"] = list(body.share_with or [])
    if body.tools is not None:
        # 安全：headers 中掩码值恢复为原明文，防止掩码覆盖真实密钥
        meta["tools"] = _restore_plugin_tools(body.tools, meta.get("tools"))
    write_meta("plugins", name, meta)
    _sync_plugins()
    return {"ok": True, "name": name}


@router.delete("/plugins/{name}")
async def remove_plugin(name: str, user=require_auth("modules:write")):
    _guard_edit("plugins", name, user, "插件")
    delete_module("plugins", name)
    _sync_plugins()
    return {"ok": True, "name": name}


@router.put("/plugins/{name}/toggle")
async def toggle_plugin(name: str, body: _ToggleBody, user=require_auth("modules:write")):
    _guard_edit("plugins", name, user, "插件")
    res = _toggle("plugins", name, body.enabled)
    _sync_plugins()
    return res


@router.post("/plugins/refresh")
async def refresh_plugins(user=require_auth("modules:write")):
    from ..core.modules import register_plugin_tools
    register_plugin_tools()
    return {"ok": True}


class PluginInstallBody(BaseModel):
    name: str
    descriptor: Optional[dict] = None


@router.post("/plugins/install")
async def install_plugin(body: PluginInstallBody, user=require_auth("modules:write")):
    """按需安装解析插件（用户在前端确认后调用，实现「直接安装」）。

    优先从内置解析插件编目（core.parsers.PARSE_PLUGINS）取规格；
    也可传入完整 descriptor 安装自定义插件。
    """
    from ..core import parsers

    # 多用户隔离：安装产物为系统级共享插件；自定义 descriptor 仅 admin 可用，
    # 防止普通用户注入全员生效的任意 shell/http 工具。内置编目安装不受限。
    if body.descriptor is not None and not _is_admin(user):
        raise HTTPException(status_code=403, detail="自定义插件描述符仅管理员可安装")
    try:
        result = parsers.install_plugin(body.name, body.descriptor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 闭环修复 Task #399：安装后立即注册工具，免重启即生效（此前仅写入 module.json，
    # 须重启或手动 /plugins/refresh 工具才可用，造成「安装成功但用不了」的开环）。
    _sync_plugins()
    return result


# ---------------------------------------------------------------------------
# MCP 服务器（真实连接 + 工具暴露）
# ---------------------------------------------------------------------------
class McpBody(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    enabled: bool = True
    command: str
    args: list = []
    env: dict = {}
    shared: bool = False          # 显式共享：对他人可见可用
    share_with: list[str] = []    # 角色级共享：仅这些角色可见可用（shared=False 时生效）


class McpUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    enabled: Optional[bool] = None
    command: Optional[str] = None
    args: Optional[list] = None
    env: Optional[dict] = None
    shared: Optional[bool] = None
    share_with: Optional[list[str]] = None


class McpCallBody(BaseModel):
    tool: str
    arguments: dict = {}


@router.get("/mcp")
async def list_mcp(user=require_auth("modules:read")):
    items = _list_modules("mcp", user)
    status = get_ctx().modules.status()
    for it in items:
        st = status.get(it["name"], {})
        it["connected"] = st.get("connected", False)
        it["tool_count"] = st.get("tool_count", 0)
        it["error"] = st.get("error")
    return {"servers": items}


@router.get("/tools")
async def list_tools(user=require_auth("modules:read")):
    """列出当前已注册到 Agent 的工具（含模块提供的 plugin__*/mcp__* 工具）。

    多用户隔离：plugin__/mcp__ 工具按归属过滤（共享 + 本人；admin 全量）。"""
    from ..core.tools import ToolRegistry
    specs = filter_tool_specs(ToolRegistry.specs(), _username(user), _is_admin(user), _role(user))
    out = []
    for s in specs:
        fn = s["function"]["name"]
        kind = "builtin"
        if fn.startswith("plugin__"):
            kind = "plugin"
        elif fn.startswith("mcp__"):
            kind = "mcp"
        out.append({"name": fn, "kind": kind, "description": s["function"].get("description", "")})
    return {"tools": out, "count": len(out)}


@router.get("/mcp/{name}")
async def get_mcp(name: str, user=require_auth("modules:read")):
    info = _guard_view("mcp", name, user, " MCP 服务器")
    info["name"] = name
    info["owner"] = info.get("owner") or None
    info["shared"] = bool(info.get("shared"))
    info["share_with"] = list(info.get("share_with") or [])
    # 安全：env 密钥脱敏后再回传
    info["env"] = _mask_env(info.get("env", {}))
    info["enabled"] = name not in set(load_state().get("mcp_disabled", []))
    st = get_ctx().modules.status().get(name, {})
    info["connected"] = st.get("connected", False)
    info["tool_count"] = st.get("tool_count", 0)
    info["error"] = st.get("error")
    return info


@router.post("/mcp")
async def create_mcp(body: McpBody, user=require_auth("modules:write")):
    name = sanitize_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="MCP 名称非法")
    if os.path.isdir(module_dir("mcp", name)):
        raise HTTPException(status_code=409, detail=f"MCP 服务器已存在：{name}")
    meta = {
        "name": name,
        "description": body.description,
        "version": body.version,
        "enabled": body.enabled,
        "command": body.command,
        "args": body.args,
        "env": body.env,
        "owner": _username(user),
        "shared": bool(body.shared),
        "share_with": list(body.share_with or []),
    }
    write_meta("mcp", name, meta)
    return {"ok": True, "name": name}


@router.put("/mcp/{name}")
async def update_mcp(name: str, body: McpUpdate, user=require_auth("modules:write")):
    meta = _guard_edit("mcp", name, user, " MCP 服务器")
    for k in ("description", "version", "enabled", "command", "args", "shared"):
        v = getattr(body, k)
        if v is not None:
            meta[k] = v
    if body.share_with is not None:
        meta["share_with"] = list(body.share_with or [])
    if body.env is not None:
        # 安全：值为掩码（******）的键视为未修改，保留原有明文，防止掩码覆盖真实密钥
        old_env = meta.get("env") or {}
        new_env = {}
        for k, v in body.env.items():
            new_env[k] = old_env.get(k, "") if str(v) == _ENV_MASK else v
        meta["env"] = new_env
    write_meta("mcp", name, meta)
    return {"ok": True, "name": name}


@router.delete("/mcp/{name}")
async def remove_mcp(name: str, user=require_auth("modules:write")):
    _guard_edit("mcp", name, user, " MCP 服务器")
    # 先断开
    try:
        await get_ctx().modules._disconnect(name)
    except Exception:
        pass
    delete_module("mcp", name)
    return {"ok": True, "name": name}


@router.put("/mcp/{name}/toggle")
async def toggle_mcp(name: str, body: _ToggleBody, user=require_auth("modules:write")):
    _guard_edit("mcp", name, user, " MCP 服务器")
    res = _toggle("mcp", name, body.enabled)
    # 停用时断开连接
    if not body.enabled:
        try:
            await get_ctx().modules._disconnect(name)
        except Exception:
            pass
    return res


@router.post("/mcp/refresh")
async def refresh_mcp(user=require_auth("modules:write")):
    await get_ctx().modules.connect_enabled_mcp()
    return {"ok": True, "status": get_ctx().modules.status()}


@router.post("/mcp/{name}/connect")
async def connect_mcp(name: str, user=require_auth("modules:write")):
    # 多用户隔离：他人私有 MCP 不可连接（视同不存在）
    _guard_view("mcp", name, user, " MCP 服务器")
    st = await get_ctx().modules.connect_one(name)
    return {"ok": True, "name": name, **st}


@router.post("/mcp/{name}/call")
async def call_mcp(name: str, body: McpCallBody, user=require_auth("modules:write")):
    # 多用户隔离：他人私有 MCP 工具不可调用（视同不存在）
    _guard_view("mcp", name, user, " MCP 服务器")
    result = await get_ctx().modules.call_mcp_tool(name, body.tool, body.arguments)
    return {"ok": True, "name": name, "tool": body.tool, "result": result}


# ---------------------------------------------------------------------------
# 记忆 Memory（MEMORY.md / USER.md / SOUL.md）
# ---------------------------------------------------------------------------
_MEMORY_FILES = {"memory": "MEMORY.md", "user": "USER.md", "soul": "SOUL.md"}


def _memory_base(user: dict) -> str:
    """安全：记忆端点按登录用户隔离（data_dir/memory/{owner}/），
    与 Agent 的 memory 工具、系统提示注入使用同一命名空间。"""
    from ..core.modules.skills import user_memory_dir
    return user_memory_dir(get_ctx().cfg.server.data_dir, user.get("u"))


@router.get("/memory")
async def get_memory(user=require_auth("modules:read")):
    base = _memory_base(user)
    out: dict = {}
    for key, fn in _MEMORY_FILES.items():
        p = os.path.join(base, fn)
        out[key] = open(p, "r", encoding="utf-8").read() if os.path.isfile(p) else ""
    return out


class _MemoryBody(BaseModel):
    memory: Optional[str] = None
    user: Optional[str] = None
    soul: Optional[str] = None


@router.put("/memory")
async def save_memory(body: _MemoryBody, user=require_auth("modules:write")):
    base = _memory_base(user)
    os.makedirs(base, exist_ok=True)
    payload = body.model_dump(exclude_none=True)
    for key, fn in _MEMORY_FILES.items():
        if key in payload:
            with open(os.path.join(base, fn), "w", encoding="utf-8") as f:
                f.write(payload[key] or "")
    return {"ok": True}


@router.get("/memory/export")
async def export_memory(user=require_auth("modules:read")):
    base = _memory_base(user)
    parts = []
    for key, fn in _MEMORY_FILES.items():
        p = os.path.join(base, fn)
        txt = open(p, "r", encoding="utf-8").read() if os.path.isfile(p) else ""
        parts.append(f"# {fn}\n\n{txt}".rstrip())
    return {"combined": "\n\n---\n\n".join(parts)}


@router.get("/memory/search")
async def search_memory(q: str = "", user=require_auth("modules:read")):
    base = _memory_base(user)
    hits: list = []
    if q:
        for key, fn in _MEMORY_FILES.items():
            p = os.path.join(base, fn)
            if not os.path.isfile(p):
                continue
            for i, line in enumerate(open(p, "r", encoding="utf-8"), 1):
                if q.lower() in line.lower():
                    hits.append({"file": fn, "line": i, "text": line.strip()})
    return {"query": q, "hits": hits}


@router.get("/memory/vector")
async def memory_vector_stats(owner: Optional[str] = None,
                              user=require_auth("modules:read")):
    """向量长期记忆体量（可观测性）。非管理员只能查看自己的。"""
    ctx = get_ctx()
    if user.get("r") != "admin":
        owner = user.get("u")
    return ctx.memory_manager.vector_stats(owner)


@router.delete("/memory/vector")
async def memory_vector_clear(owner: Optional[str] = None,
                              user=require_auth("modules:write")):
    """清空向量长期记忆（按 owner）。非管理员只能清空自己的。"""
    ctx = get_ctx()
    if user.get("r") != "admin":
        owner = user.get("u")
    deleted = ctx.memory_manager.vector_clear(owner)
    ctx.audit.log(user.get("u", ""), "memory_vector_clear",
                  f"清空向量长期记忆 owner={owner or '*'} deleted={deleted}")
    return {"ok": True, "owner": owner or "*", "deleted": deleted}
