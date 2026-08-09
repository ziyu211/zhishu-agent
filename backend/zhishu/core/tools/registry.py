"""智枢智能体 —— 工具注册中心（对标 Hermes `tools/registry.py`）。

  * ToolRegistry  —— 进程级单例注册表，提供 register / get / specs / execute。
  * discover_builtin_tools() —— 自发现：导入 core.tools.builtins 包，触发各工具模块
                              的 @tool 自注册（对标 Hermes AST 扫描 + 自动 import）。
  * resolve_toolset(name)    —— 把命名工具分组展开为具体 tool 名列表。
"""
from __future__ import annotations

import json
from typing import Optional

from .base import Tool, ToolContext, Toolset

# ---------------------------------------------------------------------------
# 工具级 RBAC（深度防御）：每个工具声明最低可调用角色。低于该角色的用户：
#   * 在 tool_visible_to 中不可见（模型看不到，自然不会调用）；
#   * 即便被强制调用，也在 execute 的集中闸门处被拦截。
# 默认不限制的 builtin 工具不在此表中。新增高危工具时务必登记。
# ---------------------------------------------------------------------------
ROLE_RANK = {"viewer": 0, "user": 1, "operator": 2, "admin": 3}

TOOL_MIN_ROLE: dict[str, str] = {
    "terminal_run": "user",              # shell 执行：与 operator 完全对等（user 及以上可用）
    "code_exec": "user",                 # 代码执行：开放到 user（含 operator/admin）
    "create_tool": "user",               # 同源 Python 执行引擎，随 code_exec 一并下放
}


def _role_ge(min_role: Optional[str], role: Optional[str]) -> bool:
    """当前角色是否 >= 最低要求角色；min_role 为空表示无限制。"""
    if not min_role:
        return True
    return ROLE_RANK.get(role or "viewer", 0) >= ROLE_RANK.get(min_role, 99)


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
        # 工具级 RBAC 闸门：低于最低角色要求的用户一律拦截（深度防御，
        # 与 tool_visible_to 的可见性过滤互补——可见性控制模型能否「看到」工具，
        # 此处控制即便被强制调用也无法执行）。
        if not _role_ge(TOOL_MIN_ROLE.get(name), getattr(ctx, "user_role", None)):
            cls._audit_tool(ctx, name, args, ok=False, err="role_denied")
            return f"[已拦截] 当前角色无权使用该工具: {name}"
        # 出网隔离开关：涉及外部网络的工具需显式放行
        if name in ("safe_web_fetch", "web_search") and ctx.security and not ctx.security.outbound_allow:
            return "[已拦截] 当前为内网隔离模式，禁止访问外部网络。如需开启，请在配置中设置 security.outbound_allow=true 并配置白名单。"
        try:
            result = await tool.handler(args or {}, ctx)
            payload = str(result)[:8000]  # 防止超大输出
        except Exception as e:
            # 动作级审计：即使工具异常也留痕（谁、哪个智能体、调了什么、是否失败）
            cls._audit_tool(ctx, name, args, ok=False, err=str(e))
            return f"[工具执行异常] {name}: {e}"
        ok = not payload.startswith(("[工具错误]", "[工具执行异常]", "[已拦截]"))
        cls._audit_tool(ctx, name, args, ok=ok, err="")
        return payload

    @classmethod
    def _audit_tool(cls, ctx, name: str, args: dict, ok: bool, err: str) -> None:
        """动作级审计（企业合规）：记录工具调用归属于哪个用户/智能体。

        失败静默、绝不因审计异常阻断工具执行；审计库未就绪（如测试）时直接跳过。
        """
        try:
            from ...context import get_ctx
            g = get_ctx()
            if g is None or getattr(g, "audit", None) is None:
                return
            agent = getattr(ctx, "agent_name", "") or "supervisor"
            args_sum = json.dumps(args, ensure_ascii=False)[:160]
            status = "ok" if ok else f"fail:{err[:80]}"
            g.audit.log(
                getattr(ctx, "user", "anonymous"),
                "tool_call",
                f"agent={agent} name={name} status={status} args={args_sum}",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 自发现 + 工具集
    # ------------------------------------------------------------------
    @classmethod
    def discover_builtin_tools(cls) -> None:
        if cls._discovered:
            return
        try:
            from . import builtins  # 触发各工具模块的自注册
        except Exception:
            # 自发现失败（如某工具模块 import 抛错）必须显式告警：否则所有内置工具会
            # 静默全部消失且永不重试。记录日志后返回（不置 _discovered），下次调用仍会重试。
            import logging
            logging.getLogger("zhishu.tools").exception(
                "内置工具自发现失败：工具将全部不可用，请检查 core/tools/builtins 下模块"
            )
            return
        cls._discovered = True

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
