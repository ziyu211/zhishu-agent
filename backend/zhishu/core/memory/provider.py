"""智枢智能体 —— 记忆 Provider 抽象（对标 Hermes `agent/memory_provider.py`）。

  记忆后端可插拔：内置 provider（SQLite 会话流水）始终第一；至多可挂一个外部
  provider（如向量长期记忆）。Agent 在每轮通过 manager 取回「记忆上下文」注入
  系统提示的 volatile 部分，并在每轮后把对话同步进记忆。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class MemoryProvider(ABC):
    """记忆 Provider 接口（方法均有默认空实现，子类按需覆盖）。"""

    async def initialize(self) -> None:
        return None

    def system_prompt_block(self, owner: Optional[str] = None) -> str:
        """返回需注入系统提示的记忆文本（volatile 部分）。"""
        return ""

    def prefetch(self, owner: Optional[str] = None, query: str = "") -> str:
        """每轮前召回与当前 query 相关的记忆文本。"""
        return ""

    async def sync_turn(self, owner: Optional[str] = None, role: str = "",
                        content: str = "") -> None:
        """每轮后持久化一条对话。"""
        return None

    def get_tool_schemas(self) -> list:
        """该 provider 贡献给 Agent 的工具声明（如有）。"""
        return []

    def shutdown(self) -> None:
        return None
