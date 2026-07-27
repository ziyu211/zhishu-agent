"""智枢智能体 —— 可插拔长期记忆后端（对标 hermes 把向量化委托给可插拔记忆后端）。

  向量化/长期记忆的具体实现被抽象为 MemoryBackend：
    * BuiltinMemoryBackend —— 复用内置 RAG 管线（EmbeddingEngine + VectorStore/sqlite），
                              零依赖、纯内网离线，作为默认后端。
    * Mem0MemoryBackend    —— 委托 mem0ai（需自行配置 LLM/向量库），懒加载，缺失时回退。

  通过 cfg.memory.backend 选择（默认 builtin），做到「默认零回归、可选升级」。
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

logger = logging.getLogger("zhishu.memory")


class MemoryBackend:
    """长期记忆后端抽象（add / search）。"""

    name: str = "abstract"

    def initialize(self) -> None:
        """可选的初始化（如连接外部服务）。"""

    def add(self, owner: Optional[str], content: str, meta: Optional[dict] = None) -> None:
        raise NotImplementedError

    def search(self, owner: Optional[str], query: str, top_k: int = 5) -> List[str]:
        raise NotImplementedError


class BuiltinMemoryBackend(MemoryBackend):
    """默认后端：复用内置 RAG 管线（sqlite 向量库）。"""

    name = "builtin"

    def __init__(self, cfg, data_dir: str):
        from ..rag import KnowledgeBase
        from ..config import VectorStoreConfig

        vs_cfg = VectorStoreConfig(
            path=__import__("os").path.join(data_dir, "zhishu_memory_vec.db"),
            backend="sqlite",
        )
        self.kb = KnowledgeBase(cfg.embedding, vs_cfg, app_cfg=cfg)
        self._seq = 0

    def add(self, owner: Optional[str], content: str, meta: Optional[dict] = None) -> None:
        self._seq += 1
        try:
            self.kb.ingest_text(
                content,
                doc_id=uuid.uuid4().hex[:12],
                owner=owner or None,
                title=meta.get("title") if meta else f"mem_{owner or 'anon'}_{self._seq}",
                file_type="memory",
                source=meta.get("source") if meta else f"mem_{owner or 'anon'}_{self._seq}",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("BuiltinMemoryBackend.add 失败: %s", e)

    def search(self, owner: Optional[str], query: str, top_k: int = 5) -> List[str]:
        if not query:
            return []
        try:
            hits = self.kb.query(query, top_k=top_k, owner=owner or None)
        except Exception as e:  # noqa: BLE001
            logger.warning("BuiltinMemoryBackend.search 失败: %s", e)
            return []
        return [h["text"] for h in hits]


class Mem0MemoryBackend(MemoryBackend):
    """mem0ai 后端（可选升级）：把长期记忆完全委托给 mem0。

    懒加载：仅当 cfg.memory.backend == "mem0" 且 mem0ai 可用时启用。
    构造或调用失败会回退到 Builtin（由 create_memory_backend 保证可用性）。
    """

    name = "mem0"

    def __init__(self):
        from mem0 import Memory  # 懒加载，缺包时抛 ImportError

        self._mem = Memory()

    @staticmethod
    def _uid(owner: Optional[str]) -> str:
        return owner or "anon"

    def add(self, owner: Optional[str], content: str, meta: Optional[dict] = None) -> None:
        uid = self._uid(owner)
        try:
            self._mem.add(content, user_id=uid)
        except Exception as e:  # noqa: BLE001
            logger.warning("Mem0MemoryBackend.add 失败: %s", e)

    def search(self, owner: Optional[str], query: str, top_k: int = 5) -> List[str]:
        if not query:
            return []
        uid = self._uid(owner)
        try:
            res = self._mem.search(query, user_id=uid)
        except Exception as e:  # noqa: BLE001
            logger.warning("Mem0MemoryBackend.search 失败: %s", e)
            return []
        out: List[str] = []
        items = res if isinstance(res, list) else res.get("results", []) if isinstance(res, dict) else []
        for r in items:
            t = r.get("memory") or r.get("text") or "" if isinstance(r, dict) else str(r)
            if t:
                out.append(t)
        return out[:top_k]


def create_memory_backend(cfg, data_dir: str) -> MemoryBackend:
    """按 cfg.memory.backend 创建后端；mem0 不可用或失败时回退 builtin。"""
    kind = getattr(cfg.memory, "backend", "builtin") or "builtin"
    if kind == "mem0":
        try:
            return Mem0MemoryBackend()
        except Exception as e:  # noqa: BLE001
            logger.warning("mem0 后端不可用，回退 builtin：%s", e)
    return BuiltinMemoryBackend(cfg, data_dir)
