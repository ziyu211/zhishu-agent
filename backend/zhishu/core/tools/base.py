"""智枢智能体 —— 工具基础定义（对标 Hermes `agent/tools` 的 Tool/ToolContext）。

  工具自注册机制：各工具模块 import 时调用 registry.register(...)。
  执行统一接收 ToolContext（含知识库、安全配置、用户/会话），并在执行前经过
  **出网隔离开关**检查（默认内网隔离）。
"""
from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, Any, Optional

from ..rag import KnowledgeBase
from ..config import SecurityConfig

# ---------------------------------------------------------------------------
# 任务级用户身份（对标 hermes-agent session_context 的 contextvars 方案）
#
#   * 每个 asyncio 任务（即每个请求 / 每次 Agent.run）拥有独立的身份视图，
#     并发请求之间互不覆盖 —— 杜绝共享可变对象导致的「身份串号」。
#   * fail-closed：读取不到身份时返回 "anonymous"，绝不继承其他请求的值。
# ---------------------------------------------------------------------------
_current_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "zhishu_current_user", default=None
)
_current_is_admin: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "zhishu_current_is_admin", default=False
)
_current_role: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "zhishu_current_role", default=None
)


def set_current_user(user: Optional[str], is_admin: bool = False,
                     user_role: Optional[str] = None) -> None:
    """在当前 asyncio 任务上下文中标记当前用户（task-local，不跨任务泄漏）。"""
    _current_user.set((user or "").strip() or "anonymous")
    _current_is_admin.set(bool(is_admin))
    _current_role.set(user_role or None)


def get_current_user() -> str:
    """取当前任务的用户身份；未设置时 fail-closed 返回 "anonymous"。"""
    return _current_user.get() or "anonymous"


def get_current_is_admin() -> bool:
    """取当前任务的管理员标记；未设置时 fail-closed 返回 False。"""
    return bool(_current_is_admin.get())


def get_current_role() -> Optional[str]:
    """取当前任务的用户角色；未设置时 fail-closed 返回 None（角色共享项不可见）。"""
    return _current_role.get()


@dataclass
class ToolContext:
    kb: Optional[KnowledgeBase] = None
    security: Optional[SecurityConfig] = None
    user: str = "anonymous"
    session: str = "default"
    is_admin: bool = False
    user_role: str = ""
    agent_name: str = ""        # 当前运行所属智能体名；空字符串=主管（supervisor）
    # 多模态产物存储（MediaStore）：工具可用它把生成文件落盘到媒体库并返回
    # /media/... 可下载 URL（file_write/code_exec 的 downloadable/save_output 用）。
    # 默认 None，由 Agent 在构造 ToolContext 时注入；for_run 浅拷贝会一并保留。
    media: Optional[Any] = None

    def for_run(
        self,
        user: Optional[str],
        session: Optional[str] = None,
        is_admin: bool = False,
        user_role: Optional[str] = None,
    ) -> "ToolContext":
        """派生一份**本次运行专用**的上下文副本（浅拷贝）。

        共享单例（AppContext.tool_ctx）保持只读，绝不在其上就地改 user/session，
        否则并发请求会互相覆盖身份（fail-open 串号）。agent_name 一并保留，
        使工具审计能正确归属到「主管」或某个「子智能体」。
        """
        u = (user or "").strip() or "anonymous"
        return replace(self, user=u, session=(session or self.session),
                       is_admin=bool(is_admin), user_role=(user_role or ""),
                       agent_name=self.agent_name)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema
    handler: Callable[[dict, ToolContext], Awaitable[str]]
    toolset: str = "builtin"  # 命名工具分组（对标 Hermes toolsets）
    is_async: bool = True


@dataclass
class Toolset:
    """命名工具分组（对标 Hermes `toolsets.py`）。

    tools   —— 该分组包含的 tool 名 / 前缀 / 子分组名
    check_fn—— 环境探针：返回 False 时该分组工具不被暴露（如未装 Docker 则不暴露 terminal）
    """

    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    check_fn: Optional[Callable[[], bool]] = None


def tool(name: str, description: str, parameters: dict, toolset: str = "builtin"):
    """便捷装饰器：注册一个工具到全局 ToolRegistry。"""

    def deco(fn: Callable[[dict, ToolContext], Awaitable[str]]):
        from .registry import ToolRegistry

        ToolRegistry.register(Tool(
            name=name, description=description,
            parameters=parameters, handler=fn, toolset=toolset,
        ))
        return fn

    return deco
