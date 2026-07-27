"""智枢智能体 —— 工具基础定义（对标 Hermes `agent/tools` 的 Tool/ToolContext）。

  工具自注册机制：各工具模块 import 时调用 registry.register(...)。
  执行统一接收 ToolContext（含知识库、安全配置、用户/会话），并在执行前经过
  **出网隔离开关**检查（默认内网隔离）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Any, Optional

from ..rag import KnowledgeBase
from ..config import SecurityConfig


@dataclass
class ToolContext:
    kb: Optional[KnowledgeBase] = None
    security: Optional[SecurityConfig] = None
    user: str = "anonymous"
    session: str = "default"


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
