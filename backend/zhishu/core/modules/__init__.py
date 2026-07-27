"""智枢智能体 —— 模块运行时包（对标 Hermes skills/plugins/mcp 三套正交扩展）。

对外统一导出：模块文件读写 + 启停状态 + 插件注册 + MCP 集成器 + 技能上下文注入。
旧 core.modules_runtime 已删除，API 层与 system_prompt 改从这里导入同名符号。
"""
from __future__ import annotations

from .runtime import (
    load_state, save_state, read_meta, write_meta, delete_module,
    sanitize_name, module_dir, DISABLED_KEY, ModuleIntegrator,
)
from .plugins import register_plugin_tools
from .skills import build_agent_context_prompt
from .mcp import MCPClient

__all__ = [
    "load_state", "save_state", "read_meta", "write_meta", "delete_module",
    "sanitize_name", "module_dir", "DISABLED_KEY", "ModuleIntegrator",
    "register_plugin_tools", "build_agent_context_prompt", "MCPClient",
]
