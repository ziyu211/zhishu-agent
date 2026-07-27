"""智枢智能体 —— Agent 运行时包（对标 Hermes `agent/`）。

  将「Agent 主循环 / 系统提示组装 / 上下文压缩 / 多智能体(MoA)」拆分为同包下的
  协作模块，主类 Agent 仅负责编排。
"""
from .agent import Agent, MAX_STEPS
from .system_prompt import build_system_prompt
from .context_engine import (
    ContextEngine, NoOpContextEngine, CompressionContextEngine, build_context_engine,
)
from .moa import MoAClient

__all__ = [
    "Agent", "MAX_STEPS", "build_system_prompt",
    "ContextEngine", "NoOpContextEngine", "CompressionContextEngine",
    "build_context_engine", "MoAClient",
]
