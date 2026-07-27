"""智枢智能体 —— LLM 客户端兼容层（shim）。

  历史导入路径 `from .core.llm import LLMClient` 仍可用；新版客户端实现已迁移至
  `core/providers/client.py`（含 Transport/Adapter 分层）。
"""
from __future__ import annotations

from .providers.client import LLMClient

__all__ = ["LLMClient"]
