"""智枢智能体 —— 向量长期记忆 Provider（对标 Hermes 外部 MemoryProvider）。

  跨会话语义召回：把每轮对话（较长者）写入长期记忆后端，检索时按 query 召回
  相关历史，注入系统提示 volatile 部分。后端可插拔（builtin / mem0 等），
  由 cfg.memory.backend 决定，默认 builtin（零回归）。

  默认关闭（cfg.memory.vector_enabled），零回归风险。
"""
from __future__ import annotations

from typing import Optional

from ..config import ZhishuConfig
from .provider import MemoryProvider
from .backends import create_memory_backend, MemoryBackend


class VectorMemoryProvider(MemoryProvider):
    def __init__(self, cfg: ZhishuConfig, data_dir: str, top_k: int = 5):
        self.top_k = top_k
        # 向量化完全委托给可插拔记忆后端（默认 builtin，可选 mem0 等）
        self.backend: MemoryBackend = create_memory_backend(cfg, data_dir)

    async def initialize(self) -> None:
        try:
            self.backend.initialize()
        except Exception:
            pass

    def prefetch(self, owner: Optional[str] = None, query: str = "") -> str:
        if not query:
            return ""
        try:
            hits = self.backend.search(owner, query, top_k=self.top_k)
        except Exception:
            return ""
        if not hits:
            return ""
        lines = [f"[记忆 {i+1}] {h}" for i, h in enumerate(hits)]
        return "【长期记忆召回】\n" + "\n\n".join(lines)

    async def sync_turn(self, owner: Optional[str] = None, role: str = "",
                        content: str = "") -> None:
        if not content or role not in ("user", "assistant"):
            return
        if len(content) < 12:  # 跳过过短的寒暄/占位
            return
        try:
            self.backend.add(owner, content, meta={"title": f"mem_{owner or 'anon'}",
                                                   "source": f"mem_{owner or 'anon'}"})
        except Exception:
            pass
