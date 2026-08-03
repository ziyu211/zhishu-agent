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


# ---------------------------------------------------------------------------
# 归属与可见性（多用户隔离，默认私有 + 显式共享 + 角色级共享）
#   * meta 无 owner（None/空）= 历史系统级共享：全员可见可用，仅 admin 可管理
#                                （兼容改造前的存量数据，不破坏既有使用）
#   * meta 有 owner          = 私有：仅 owner 与 admin 可见/可管理
#   * meta.shared = True     = 显式共享：全员可见可用（配置/创建时打标）
#   * meta.share_with = [...] = 角色级共享：仅当「用户角色命中列表」时可见
#                              （仍仅 owner + admin 可管理，角色成员只读，防越权写入）
#   新创建项一律归属创建者（私有），需显式 shared=True 或 share_with=[角色] 才对他人可见。
# ---------------------------------------------------------------------------
def module_owner(sub: str, name: str) -> Optional[str]:
    """返回模块归属用户名；None 表示系统级共享。"""
    owner = (read_meta(sub, name) or {}).get("owner")
    return str(owner) if owner else None


def module_shared(sub: str, name: str) -> bool:
    """返回模块是否显式全员共享。"""
    return bool((read_meta(sub, name) or {}).get("shared"))


def module_share_with(sub: str, name: str) -> list:
    """返回模块角色共享列表（空列表表示不按角色共享）。"""
    return list((read_meta(sub, name) or {}).get("share_with") or [])


def can_view(owner_val: Optional[str], username: Optional[str], is_admin: bool,
             shared: bool = False, share_with: Optional[list] = None,
             user_role: Optional[str] = None) -> bool:
    """可见性判定（fail-closed）：

      * admin             → 全可见（含他人私有，便于代管）
      * shared=True       → 全员可见（历史/显式全员共享）
      * owner_val=None    → 历史系统级共享，全员可见（仅 admin 可管理）
      * owner==username   → 私有本人可见
      * share_with 命中    → 角色级共享：用户角色命中列表时可见（仅 owner+admin 可改）

    不满足以上任一条件 → 不可见。身份或角色为空时，角色共享项一律不可见。
    """
    if is_admin or shared:
        return True
    if owner_val is None:
        return True
    if username and owner_val == username:
        return True
    if username and share_with and user_role and user_role in share_with:
        return True
    return False


def can_edit(owner_val: Optional[str], username: Optional[str], is_admin: bool,
             shared: bool = False) -> bool:
    """可写性：admin 全可写；历史无 owner（系统级共享）仅 admin；
    私有/共享/角色共享项均仅 owner 本人可改（角色共享成员只读，防越权写入）。
    fail-closed：身份为空一律拒绝。"""
    if is_admin:
        return True
    if owner_val is None:
        return False
    if owner_val == username:
        return True
    return False


def can_view_meta(meta: dict, username: Optional[str], is_admin: bool,
                  user_role: Optional[str] = None) -> bool:
    return can_view(meta.get("owner") or None, username, is_admin,
                    bool(meta.get("shared")), meta.get("share_with") or None, user_role)


def can_edit_meta(meta: dict, username: Optional[str], is_admin: bool) -> bool:
    return can_edit(meta.get("owner") or None, username, is_admin, bool(meta.get("shared")))


def _tool_module(tool_name: str) -> Optional[tuple[str, str]]:
    """从工具名解析所属模块：plugin__<插件>__<工具> / mcp__<服务器>__<工具>。
    非模块工具（builtin）返回 None。"""
    for prefix, sub in (("plugin__", "plugins"), ("mcp__", "mcp")):
        if tool_name.startswith(prefix):
            rest = tool_name[len(prefix):]
            mod = rest.split("__", 1)[0]
            return sub, mod
    return None


def tool_visible_to(tool_name: str, username: Optional[str], is_admin: bool = False,
                    user_role: Optional[str] = None) -> bool:
    """运行时守卫：plugin__/mcp__ 工具仅对「共享模块 + 本人模块 + 角色命中模块」可见/可执行。
    builtin 工具默认不受限；但声明了最低角色要求（registry.TOOL_MIN_ROLE）的高危工具
    仅对达角色的用户可见（避免把 terminal_run/code_exec 暴露给 viewer/user）；
    admin 不受私有归属过滤（否则 admin 自建私有工具失效）。"""
    hit = _tool_module(tool_name)
    if hit is None:
        # builtin 工具：按最低角色要求做可见性深度防御
        from ..tools.registry import TOOL_MIN_ROLE, _role_ge
        return _role_ge(TOOL_MIN_ROLE.get(tool_name), user_role)
    if is_admin:
        return True
    sub, mod = hit
    meta = read_meta(sub, mod) or {}
    return can_view(meta.get("owner") or None, username, False,
                    bool(meta.get("shared")), meta.get("share_with") or None, user_role)


def filter_tool_specs(specs: list, username: Optional[str], is_admin: bool = False,
                      user_role: Optional[str] = None) -> list:
    """按用户过滤工具声明列表（plugin__/mcp__ 按归属 + 共享 + 角色，builtin 保留）。"""
    return [s for s in specs if tool_visible_to(s["function"]["name"], username, is_admin, user_role)]


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
