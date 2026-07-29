"""智枢智能体 —— 工具层包（对标 Hermes `tools/` + `tools/registry.py`）。

  导入本包即完成工具自注册：base（定义）/ registry（注册中心 + 自发现）/
  toolsets（命名分组）/ builtins（内置工具实现）。
"""
from __future__ import annotations

from .base import (
    Tool, ToolContext, Toolset, tool,
    set_current_user, get_current_user,
)
from .registry import ToolRegistry
from .toolsets import TOOLSETS

# 触发内置工具自注册（导入 builtins 包）
ToolRegistry.discover_builtin_tools()

__all__ = [
    "Tool", "ToolContext", "Toolset", "tool",
    "ToolRegistry", "TOOLSETS",
    "set_current_user", "get_current_user",
]
