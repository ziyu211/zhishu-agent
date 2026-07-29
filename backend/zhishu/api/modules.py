"""智枢智能体 —— 技能 / 插件 / MCP / 记忆 模块路由（完整 CRUD + 运行时接入）。

  * skills / plugins / mcp 均以"目录即模块"管理（data/<sub>/<name>/ 下的 json 元信息）。
  * 启用/停用状态写入 data/modules_state.json 的 disabled 列表（与文件内 enabled 字段取交集）。
  * memory 直接读写 data/MEMORY.md、USER.md、SOUL.md 三份长期记忆文件。
  * MCP 额外提供 详情/连接/调用/刷新 接口，使服务器工具可立即被 Agent 使用。
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Optional

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
    DISABLED_KEY,
)
from ..core.modules.skills_io import import_archive, export_skills


router = APIRouter(prefix="/api/v1", tags=["modules"])


# ---------------------------------------------------------------------------
# 通用：列表 / 启停
# ---------------------------------------------------------------------------
def _list_modules(sub: str) -> list:
    from ..core.modules import module_dir as _md
    base = _md(sub, "")
    state = load_state()
    disabled = set(state.get(DISABLED_KEY[sub], []))
    out: list = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d) or name in disabled:
            continue
        info = read_meta(sub, name)
        if info.get("enabled") is False:
            continue
        item: dict[str, Any] = {
            "name": name,
            "description": info.get("description", ""),
            "version": info.get("version", ""),
            "enabled": name not in disabled,
        }
        if sub == "plugins":
            item["tool_count"] = len(info.get("tools") or [])
        if sub == "mcp":
            item["command"] = info.get("command", "")
            item["args"] = info.get("args", [])
            item["env"] = info.get("env", {})
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


class SkillUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    content: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/skills")
async def list_skills(user=require_auth("modules:read")):
    return {"skills": _list_modules("skills")}


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
        res = import_archive(data, fmt, get_ctx().cfg.server.data_dir)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"导入失败：{e}")
    return {"ok": True, **res}


@router.get("/skills/export")
async def export_skills_all(user=require_auth("modules:read")):
    """导出全部技能为 zip（智枢原生格式，兼容 Hermes 的 SKILL.md 约定）。"""
    data = export_skills(get_ctx().cfg.server.data_dir)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=zhishu-skills.zip"},
    )


@router.get("/skills/{name}/export")
async def export_skill_one(name: str, user=require_auth("modules:read")):
    """导出单个技能为 zip。"""
    if not os.path.isdir(module_dir("skills", name)):
        raise HTTPException(status_code=404, detail=f"未找到技能：{name}")
    data = export_skills(get_ctx().cfg.server.data_dir, names=[name])
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=zhishu-skill-{name}.zip"},
    )


@router.get("/skills/{name}")
async def get_skill(name: str, user=require_auth("modules:read")):
    if not os.path.isdir(module_dir("skills", name)):
        raise HTTPException(status_code=404, detail=f"未找到技能：{name}")
    info = read_meta("skills", name)
    info["name"] = name
    info["enabled"] = name not in set(load_state().get("skills_disabled", []))
    return info


@router.post("/skills")
async def create_skill(body: SkillBody, user=require_auth("modules:write")):
    name = sanitize_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="技能名称非法")
    if os.path.isdir(module_dir("skills", name)):
        raise HTTPException(status_code=409, detail=f"技能已存在：{name}")
    meta = {
        "name": name,
        "description": body.description,
        "version": body.version,
        "content": body.content,
        "enabled": body.enabled,
    }
    write_meta("skills", name, meta)
    return {"ok": True, "name": name}


@router.put("/skills/{name}")
async def update_skill(name: str, body: SkillUpdate, user=require_auth("modules:write")):
    if not os.path.isdir(module_dir("skills", name)):
        raise HTTPException(status_code=404, detail=f"未找到技能：{name}")
    meta = read_meta("skills", name)
    for k in ("description", "version", "content", "enabled"):
        v = getattr(body, k)
        if v is not None:
            meta[k] = v
    write_meta("skills", name, meta)
    return {"ok": True, "name": name}


@router.delete("/skills/{name}")
async def remove_skill(name: str, user=require_auth("modules:write")):
    if not os.path.isdir(module_dir("skills", name)):
        raise HTTPException(status_code=404, detail=f"未找到技能：{name}")
    delete_module("skills", name)
    return {"ok": True, "name": name}


@router.put("/skills/{name}/toggle")
async def toggle_skill(name: str, body: _ToggleBody, user=require_auth("modules:write")):
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


class PluginUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    enabled: Optional[bool] = None
    tools: Optional[list] = None


@router.get("/plugins")
async def list_plugins(user=require_auth("modules:read")):
    return {"plugins": _list_modules("plugins")}


@router.get("/plugins/{name}")
async def get_plugin(name: str, user=require_auth("modules:read")):
    if not os.path.isdir(module_dir("plugins", name)):
        raise HTTPException(status_code=404, detail=f"未找到插件：{name}")
    info = read_meta("plugins", name)
    info["name"] = name
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
    }
    write_meta("plugins", name, meta)
    return {"ok": True, "name": name}


@router.put("/plugins/{name}")
async def update_plugin(name: str, body: PluginUpdate, user=require_auth("modules:write")):
    if not os.path.isdir(module_dir("plugins", name)):
        raise HTTPException(status_code=404, detail=f"未找到插件：{name}")
    meta = read_meta("plugins", name)
    for k in ("description", "version", "enabled", "tools"):
        v = getattr(body, k)
        if v is not None:
            meta[k] = v
    write_meta("plugins", name, meta)
    return {"ok": True, "name": name}


@router.delete("/plugins/{name}")
async def remove_plugin(name: str, user=require_auth("modules:write")):
    if not os.path.isdir(module_dir("plugins", name)):
        raise HTTPException(status_code=404, detail=f"未找到插件：{name}")
    delete_module("plugins", name)
    return {"ok": True, "name": name}


@router.put("/plugins/{name}/toggle")
async def toggle_plugin(name: str, body: _ToggleBody, user=require_auth("modules:write")):
    return _toggle("plugins", name, body.enabled)


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

    try:
        result = parsers.install_plugin(body.name, body.descriptor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


class McpUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    enabled: Optional[bool] = None
    command: Optional[str] = None
    args: Optional[list] = None
    env: Optional[dict] = None


class McpCallBody(BaseModel):
    tool: str
    arguments: dict = {}


@router.get("/mcp")
async def list_mcp(user=require_auth("modules:read")):
    items = _list_modules("mcp")
    status = get_ctx().modules.status()
    for it in items:
        st = status.get(it["name"], {})
        it["connected"] = st.get("connected", False)
        it["tool_count"] = st.get("tool_count", 0)
        it["error"] = st.get("error")
    return {"servers": items}


@router.get("/tools")
async def list_tools(user=require_auth("modules:read")):
    """列出当前已注册到 Agent 的全部工具（含模块提供的 plugin__*/mcp__* 工具）。"""
    from ..core.tools import ToolRegistry
    specs = ToolRegistry.specs()
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
    if not os.path.isdir(module_dir("mcp", name)):
        raise HTTPException(status_code=404, detail=f"未找到 MCP 服务器：{name}")
    info = read_meta("mcp", name)
    info["name"] = name
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
    }
    write_meta("mcp", name, meta)
    return {"ok": True, "name": name}


@router.put("/mcp/{name}")
async def update_mcp(name: str, body: McpUpdate, user=require_auth("modules:write")):
    if not os.path.isdir(module_dir("mcp", name)):
        raise HTTPException(status_code=404, detail=f"未找到 MCP 服务器：{name}")
    meta = read_meta("mcp", name)
    for k in ("description", "version", "enabled", "command", "args", "env"):
        v = getattr(body, k)
        if v is not None:
            meta[k] = v
    write_meta("mcp", name, meta)
    return {"ok": True, "name": name}


@router.delete("/mcp/{name}")
async def remove_mcp(name: str, user=require_auth("modules:write")):
    if not os.path.isdir(module_dir("mcp", name)):
        raise HTTPException(status_code=404, detail=f"未找到 MCP 服务器：{name}")
    # 先断开
    try:
        await get_ctx().modules._disconnect(name)
    except Exception:
        pass
    delete_module("mcp", name)
    return {"ok": True, "name": name}


@router.put("/mcp/{name}/toggle")
async def toggle_mcp(name: str, body: _ToggleBody, user=require_auth("modules:write")):
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
    st = await get_ctx().modules.connect_one(name)
    return {"ok": True, "name": name, **st}


@router.post("/mcp/{name}/call")
async def call_mcp(name: str, body: McpCallBody, user=require_auth("modules:write")):
    result = await get_ctx().modules.call_mcp_tool(name, body.tool, body.arguments)
    return {"ok": True, "name": name, "tool": body.tool, "result": result}


# ---------------------------------------------------------------------------
# 记忆 Memory（MEMORY.md / USER.md / SOUL.md）
# ---------------------------------------------------------------------------
_MEMORY_FILES = {"memory": "MEMORY.md", "user": "USER.md", "soul": "SOUL.md"}


@router.get("/memory")
async def get_memory(user=require_auth("modules:read")):
    ctx = get_ctx()
    base = ctx.cfg.server.data_dir
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
    ctx = get_ctx()
    base = ctx.cfg.server.data_dir
    os.makedirs(base, exist_ok=True)
    payload = body.model_dump(exclude_none=True)
    for key, fn in _MEMORY_FILES.items():
        if key in payload:
            with open(os.path.join(base, fn), "w", encoding="utf-8") as f:
                f.write(payload[key] or "")
    return {"ok": True}


@router.get("/memory/export")
async def export_memory(user=require_auth("modules:read")):
    ctx = get_ctx()
    base = ctx.cfg.server.data_dir
    parts = []
    for key, fn in _MEMORY_FILES.items():
        p = os.path.join(base, fn)
        txt = open(p, "r", encoding="utf-8").read() if os.path.isfile(p) else ""
        parts.append(f"# {fn}\n\n{txt}".rstrip())
    return {"combined": "\n\n---\n\n".join(parts)}


@router.get("/memory/search")
async def search_memory(q: str = "", user=require_auth("modules:read")):
    ctx = get_ctx()
    base = ctx.cfg.server.data_dir
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
