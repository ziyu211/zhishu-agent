"""智枢智能体 —— 工具注册中心（对标 Hermes `tools/registry.py`）。

  * ToolRegistry  —— 进程级单例注册表，提供 register / get / specs / execute。
  * discover_builtin_tools() —— 自发现：导入 core.tools.builtins 包，触发各工具模块
                              的 @tool 自注册（对标 Hermes AST 扫描 + 自动 import）。
  * resolve_toolset(name)    —— 把命名工具分组展开为具体 tool 名列表。
"""
from __future__ import annotations

from typing import Optional

from .base import Tool, ToolContext, Toolset


class ToolRegistry:
    _tools: dict[str, Tool] = {}
    _discovered = False

    @classmethod
    def register(cls, tool: Tool):
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Optional[Tool]:
        return cls._tools.get(name)

    @classmethod
    def all(cls) -> list[Tool]:
        cls.discover_builtin_tools()
        return list(cls._tools.values())

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._tools.pop(name, None)

    @classmethod
    def clear_prefix(cls, prefix: str) -> None:
        """注销所有以 prefix 开头的工具（用于插件/MCP 刷新时清理旧注册）。"""
        for n in [n for n in cls._tools if n.startswith(prefix)]:
            cls._tools.pop(n, None)

    @classmethod
    def specs(cls, toolset: Optional[str] = None) -> list[dict]:
        """转换为 OpenAI function-calling 工具声明。可选按 toolset 过滤。"""
        cls.discover_builtin_tools()
        out = []
        for t in cls._tools.values():
            if toolset and t.toolset != toolset:
                continue
            out.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            })
        return out

    @classmethod
    async def execute(cls, name: str, args: dict, ctx: ToolContext) -> str:
        cls.discover_builtin_tools()
        tool = cls._tools.get(name)
        if not tool:
            return f"[工具错误] 未注册工具: {name}"
        # 出网隔离开关：涉及外部网络的工具需显式放行
        if name in ("safe_web_fetch", "web_search") and ctx.security and not ctx.security.outbound_allow:
            return "[已拦截] 当前为内网隔离模式，禁止访问外部网络。如需开启，请在配置中设置 security.outbound_allow=true 并配置白名单。"
        try:
            result = await tool.handler(args or {}, ctx)
            return str(result)[:8000]  # 防止超大输出
        except Exception as e:
            return f"[工具执行异常] {name}: {e}"

    # ------------------------------------------------------------------
    # 自发现 + 工具集
    # ------------------------------------------------------------------
    @classmethod
    def discover_builtin_tools(cls) -> None:
        if cls._discovered:
            return
        cls._discovered = True
        try:
            from . import builtins  # 触发各工具模块的自注册
        except Exception:
            pass

    @classmethod
    def resolve_toolset(cls, name: str) -> list[str]:
        """展开命名工具分组为具体 tool 名列表（含递归子分组）。"""
        from . import toolsets

        seen: set[str] = set()
        out: list[str] = []

        def _expand(entry: str):
            ts = toolsets.TOOLSETS.get(entry)
            # 仅当 entry 是「尚未展开过的分组」才递归；已展开过的同名条目按具体
            # 工具名处理（兼容工具与分组同名的情况，如 todo 分组含 todo 工具）。
            if ts is not None and entry not in seen:
                seen.add(entry)
                if ts.check_fn is not None and not ts.check_fn():
                    return
                for sub in ts.tools:
                    _expand(sub)
            elif entry not in out:
                out.append(entry)

        _expand(name)
        return out
