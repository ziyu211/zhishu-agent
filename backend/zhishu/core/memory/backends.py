"""智枢智能体 —— 可插拔长期记忆后端（对标 hermes 把向量化委托给可插拔记忆后端）。

  向量化/长期记忆的具体实现被抽象为 MemoryBackend：
    * BuiltinMemoryBackend —— 复用内置 RAG 管线（EmbeddingEngine + VectorStore/sqlite），
                              零依赖、纯内网离线，作为默认后端。
    * Mem0MemoryBackend    —— 委托 mem0ai（需自行配置 LLM/向量库），懒加载，缺失时回退。

  通过 cfg.memory.backend 选择（默认 builtin），做到「默认零回归、可选升级」。

  v1.0.39 插件化：注册表 register_memory_backend(name, factory) 允许第三方后端
  以 ``cfg.memory.backend = "<name>"`` 接入，对齐 Hermes 的 provider 插件体系；
  未注册 / 构造失败自动回退 builtin，绝不崩服务。
"""
from __future__ import annotations

import logging
import uuid
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("zhishu.memory")

# 后端注册表：name -> factory(cfg, data_dir) -> MemoryBackend
_BACKEND_REGISTRY: Dict[str, Callable] = {}


def register_memory_backend(name: str, factory: Callable) -> None:
    """注册一个可插拔记忆后端工厂（进程级注册表）。

    factory 签名：``factory(cfg, data_dir) -> MemoryBackend``。
    注册后即可通过 ``cfg.memory.backend = name`` 启用；构造失败自动回退 builtin。
    """
    if not name or not callable(factory):
        return
    _BACKEND_REGISTRY[str(name).strip().lower()] = factory
    logger.info("memory backend registered: %s", name)


def registered_backends() -> Dict[str, Callable]:
    """返回当前已注册的后端工厂（复制，防外部篡改）。"""
    return dict(_BACKEND_REGISTRY)


class MemoryBackend:
    """长期记忆后端抽象（add / search）。"""

    name: str = "abstract"

    def initialize(self) -> None:
        """可选的初始化（如连接外部服务）。"""

    def add(self, owner: Optional[str], content: str, meta: Optional[dict] = None) -> None:
        raise NotImplementedError

    def search(self, owner: Optional[str], query: str, top_k: int = 5) -> List[str]:
        raise NotImplementedError

    def stats(self, owner: Optional[str] = None) -> dict:
        """返回该 owner 的长期记忆体量（count 等）。"""
        raise NotImplementedError

    def clear(self, owner: Optional[str] = None) -> int:
        """清空该 owner 的长期记忆，返回删除条数。"""
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

    def stats(self, owner: Optional[str] = None) -> dict:
        try:
            owner = owner or None
            # 与 rag.py:stats 对齐：向量库按 owner 计向量条数 / 文档数 / 嵌入维度
            vectors = self.kb.store.count(owner)
            documents = self.kb.store.doc_count(owner)
            dim = getattr(getattr(self.kb, "emb", None), "dim", None)
            emb = getattr(self.kb, "emb", None)
            out = {
                "backend": "builtin",
                "vectors": vectors,
                "documents": documents,
                "embedding_dim": dim,
                "count": vectors,  # 兼容前端 clear 守卫依赖
                "owner": owner or "*",
                # 与 rag.py:stats 对齐：透传检索能力，让前端如实展示「全文/混合」模式
                "fts_available": getattr(self.kb.store, "_fts_available", False),
                "semantic_available": getattr(emb, "semantic_available", False) if emb else False,
                "unconfigured": getattr(emb, "unconfigured", False) if emb else False,
                "retrieval_mode": "hybrid" if (getattr(emb, "semantic_available", False) if emb else False) else "fts",
            }
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("BuiltinMemoryBackend.stats 失败: %s", e)
            return {
                "backend": "builtin",
                "vectors": 0,
                "documents": 0,
                "embedding_dim": None,
                "count": 0,
                "owner": owner or "*",
                "error": str(e),
            }

    def clear(self, owner: Optional[str] = None) -> int:
        try:
            docs = self.kb.list_documents(owner=owner or None, limit=10000)
            n = 0
            for d in docs:
                did = d.get("doc_id")
                if did and self.kb.delete_document(did, owner or None):
                    n += 1
            return n
        except Exception as e:  # noqa: BLE001
            logger.warning("BuiltinMemoryBackend.clear 失败: %s", e)
            return 0


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

    def stats(self, owner: Optional[str] = None) -> dict:
        # mem0 不直接暴露计数，返回后端标识（count 为 None 表示未知）。
        return {"backend": "mem0", "count": None, "owner": owner or "*"}

    def clear(self, owner: Optional[str] = None) -> int:
        uid = self._uid(owner)
        try:
            self._mem.delete_all(user_id=uid)
            return -1  # mem0 不返回删除计数
        except Exception as e:  # noqa: BLE001
            logger.warning("Mem0MemoryBackend.clear 失败: %s", e)
            return 0


def create_memory_backend(cfg, data_dir: str) -> MemoryBackend:
    """按 cfg.memory.backend 创建后端；未注册/失败时回退 builtin。

    优先级：内置 mem0（旧配置兼容）> 注册表插件 > builtin 默认。
    """
    kind = (getattr(cfg.memory, "backend", "builtin") or "builtin").strip().lower()
    if kind == "mem0":
        try:
            return Mem0MemoryBackend()
        except Exception as e:  # noqa: BLE001
            logger.warning("mem0 后端不可用，回退 builtin：%s", e)
    # 注册表插件（v1.0.39）：第三方后端经 register_memory_backend 接入
    factory = _BACKEND_REGISTRY.get(kind)
    if factory is not None:
        try:
            backend = factory(cfg, data_dir)
            if backend is not None:
                return backend
        except Exception as e:  # noqa: BLE001
            logger.warning("记忆后端 '%s' 初始化失败，回退 builtin：%s", kind, e)
    return BuiltinMemoryBackend(cfg, data_dir)
