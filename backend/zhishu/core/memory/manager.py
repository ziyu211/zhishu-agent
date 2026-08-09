"""智枢智能体 —— 记忆管理器（对标 Hermes `agent/memory_manager.py`）。

  编排记忆 provider：内置会话记忆（SQLite，由 Agent 直接管理历史）始终存在；
  至多可挂一个外部 provider（向量长期记忆，opt-in）。强制「至多一个外部
  provider」，避免 schema 膨胀（与 Hermes 约束一致）。
"""
from __future__ import annotations

from typing import Optional

from ..config import ZhishuConfig
from .provider import MemoryProvider
from .sqlite_provider import SQLiteMemoryProvider, MemoryStore
from .vector_provider import VectorMemoryProvider


class MemoryManager:
    def __init__(self, cfg: ZhishuConfig, data_dir: str, builtin_store: MemoryStore):
        self.cfg = cfg
        self.builtin_store = builtin_store
        self.builtin = SQLiteMemoryProvider(store=builtin_store)
        self.external: Optional[MemoryProvider] = None
        if getattr(cfg.memory, "vector_enabled", False):
            try:
                self.external = VectorMemoryProvider(
                    cfg, data_dir, cfg.memory.vector_top_k
                )
            except Exception:
                self.external = None

    async def initialize(self) -> None:
        if self.external:
            await self.external.initialize()

    def prefetch(self, owner: Optional[str] = None, query: str = "") -> str:
        """召回与 query 相关的长期记忆（仅外部向量 provider 贡献）。"""
        if self.external:
            return self.external.prefetch(owner, query)
        return ""

    async def sync_turn(self, owner: Optional[str] = None, role: str = "",
                        content: str = "") -> None:
        """每轮后把对话同步进外部长期记忆（内置会话记忆由 Agent 直接管理）。"""
        if self.external:
            await self.external.sync_turn(owner, role, content)

    @property
    def vector_enabled(self) -> bool:
        return self.external is not None

    def vector_stats(self, owner: Optional[str] = None) -> dict:
        """向量长期记忆体量（可观测性）。未开启时返回 enabled=False。"""
        if self.external:
            out = self.external.stats(owner)
            out["enabled"] = True
            return out
        return {"enabled": False, "backend": None, "count": 0, "owner": owner or "*"}

    def vector_clear(self, owner: Optional[str] = None) -> int:
        """清空向量长期记忆（按 owner）。未开启时返回 0。"""
        if self.external:
            return self.external.clear(owner)
        return 0
