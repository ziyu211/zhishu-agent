"""智枢智能体 —— 模块运行时（对标 Hermes 的 skills/plugins/mcp 注册与生命周期）。

提供：
  * 模块文件读写（module_dir / read_meta / write_meta / delete_module / sanitize_name）
  * 模块启停状态（modules_state.json + DISABLED_KEY）
  * ModuleIntegrator —— 连接已启用 MCP、注册插件/MCP 工具，供 Agent 调用

API 层（api/modules.py）与 system_prompt 均依赖本模块的符号；旧 core.modules_runtime
已删除，这里是其等价重建（包内分文件：skills/plugins/mcp 各司其职）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import Optional

from ..tools import ToolRegistry, Tool
from .mcp import MCPClient, _make_mcp_handler


# 模块子目录与对应「禁用」状态键
MODULE_SUBS = ("skills", "plugins", "mcp")
DISABLED_KEY = {
    "skills": "skills_disabled",
    "plugins": "plugins_disabled",
    "mcp": "mcp_disabled",
}


def sanitize_name(name: str) -> str:
    """模块名只允许 [A-Za-z0-9_.-]，避免路径穿越。"""
    return re.sub(r"[^A-Za-z0-9_.\-]", "", (name or "").strip())[:64]


def _state_path() -> str:
    from ...context import get_ctx
    return os.path.join(get_ctx().cfg.server.data_dir, "modules_state.json")


def load_state() -> dict:
    p = _state_path()
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {DISABLED_KEY[s]: [] for s in MODULE_SUBS}


def save_state(state: dict) -> None:
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def module_dir(sub: str, name: str = "") -> str:
    from ...context import get_ctx
    base = os.path.join(get_ctx().cfg.server.data_dir, sub)
    if name:
        return os.path.join(base, sanitize_name(name))
    return base


def read_meta(sub: str, name: str) -> dict:
    """读取模块元信息。优先 module.json（Hermes 规范），并兼容历史文件名
    skill.json / plugin.json / mcp.json（sub 去复数 s + .json），使既有
    演示数据无需改动即可被识别。"""
    d = module_dir(sub, name)
    legacy = sub.rstrip("s") + ".json"  # skills->skill.json, plugins->plugin.json, mcp->mcp.json
    for fn in ("module.json", legacy):
        fp = os.path.join(d, fn)
        if os.path.isfile(fp):
            try:
                return json.load(open(fp, encoding="utf-8"))
            except Exception:
                return {}
    return {}


def write_meta(sub: str, name: str, meta: dict) -> dict:
    d = module_dir(sub, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    # 技能：正文同时写入 SKILL.md（Hermes 渐进披露约定，read_skill 优先读此文件）
    if sub == "skills" and meta.get("content"):
        try:
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(meta["content"])
        except Exception:
            pass
    return meta


def delete_module(sub: str, name: str) -> None:
    d = module_dir(sub, name)
    if os.path.isdir(d):
        shutil.rmtree(d)


class ModuleIntegrator:
    """连接 MCP、注册插件与 MCP 工具，供 Agent 运行时调用。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.clients: dict[str, MCPClient] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------
    async def refresh(self) -> dict:
        """启动时调用：注册插件工具 + 连接已启用 MCP。"""
        try:
            from .plugins import register_plugin_tools
            register_plugin_tools()
        except Exception:
            pass
        try:
            await self.connect_enabled_mcp()
        except Exception:
            pass
        return self.status()

    async def connect_enabled_mcp(self) -> None:
        base = module_dir("mcp", "")
        if not os.path.isdir(base):
            return
        state = load_state()
        disabled = set(state.get(DISABLED_KEY["mcp"], []))
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if not os.path.isdir(d) or name in disabled:
                continue
            meta = read_meta("mcp", name)
            if meta.get("enabled") is False:
                continue
            try:
                await self.connect_one(name, meta=meta)
            except Exception:
                pass

    async def connect_one(self, name: str, meta: Optional[dict] = None) -> dict:
        name = sanitize_name(name)
        meta = meta or read_meta("mcp", name)
        from ...context import get_ctx
        client = MCPClient(name, meta or {}, data_dir=get_ctx().cfg.server.data_dir)
        try:
            await client.connect()
        except Exception as e:
            client.error = f"连接失败: {e}"
        # 注册工具（即使 connect 报错也登记，便于前端看到工具数/错误）
        for tool in client.tools:
            tname = tool.get("name")
            if not tname:
                continue
            spec = tool.get("inputSchema") or tool.get("schema") or {"type": "object", "properties": {}}
            ToolRegistry.register(Tool(
                name=f"mcp__{name}__{tname}",
                description=tool.get("description", ""),
                parameters=spec if isinstance(spec, dict) else {"type": "object", "properties": {}},
                handler=_make_mcp_handler(client, tname),
                toolset="mcp",
            ))
        self.clients[name] = client
        return self.status().get(name, {})

    async def _disconnect(self, name: str) -> None:
        client = self.clients.pop(name, None)
        ToolRegistry.clear_prefix(f"mcp__{name}__")
        if client:
            try:
                await client.close()
            except Exception:
                pass

    async def call_mcp_tool(self, name: str, tool: str, arguments: dict) -> str:
        client = self.clients.get(name)
        if client is None:
            await self.connect_one(name)
            client = self.clients.get(name)
        if client is None:
            raise RuntimeError(f"MCP 服务器未连接：{name}")
        return await client.call_tool(tool, arguments or {})

    def status(self) -> dict:
        out = {}
        for name, c in self.clients.items():
            out[name] = {
                "connected": c.error is None,
                "tool_count": len(c.tools),
                "error": c.error,
            }
        return out
