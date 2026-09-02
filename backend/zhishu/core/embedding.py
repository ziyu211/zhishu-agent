"""智枢智能体 —— Embedding（配置驱动）。

支持后端（完全跟随配置，无内置本地预设）：
  * provider : 走配置的模型 Provider 的 OpenAI 兼容 /embeddings 接口（网络）。
               与 LLM 调用共用同一套 Provider 解析链——配置了哪个模型就用哪个，
               本地模型走本地、云端模型走云端，完全跟着配置走。
               通过 `embedding.embed_model` 显式指定用于 embeddings 的模型名；
               **若未配置 embed_model（即「未自定义配置」），则直接降级为 hash 向量，
               不发起任何网络请求**（由 fallback_hash 控制，默认开启）。
  * local    : sentence-transformers 本地加载 bge-zh / m3e（需 torch，离线，显式开启）
  * ollama   : 本地 Ollama 的 embeddings 接口（离线，显式开启）
  * hash     : 纯 Python 确定性哈希向量（无模型，零依赖降级，保证流程不中断）

无论哪种后端，对外接口统一为 embed(texts: list[str]) -> list[list[float]]。
"""
from __future__ import annotations

import hashlib
import logging
from typing import List, Optional

import numpy as np

import httpx

from .config import EmbeddingConfig, ZhishuConfig

logger = logging.getLogger("zhishu.embedding")


class EmbeddingEngine:
    def __init__(self, cfg: EmbeddingConfig, app_cfg: Optional[ZhishuConfig] = None):
        self.cfg = cfg
        self.app_cfg = app_cfg
        self._backend = None
        self._model = None
        self._dim = cfg.dim
        self._http: Optional[httpx.Client] = None
        self._provider_failed = False  # 缓存：provider 网络 embedding 失败时置位，后续直接走 hash，避免反复打网络
        # 降级隔离：记录「本应使用的后端」与「上一批实际使用的后端」。
        # hash 伪向量与真实语义向量不在同一向量空间（维度也常不同），混存会污染
        # 检索库，故每批向量都要带签名，由 VectorStore 按签名隔离。
        self._intended: Optional[str] = None
        self._last_kind: Optional[str] = None

    def _lazy_init(self):
        if self._backend is not None:
            return
        backend = self.cfg.backend
        # 「本应使用的后端」：backend=provider/auto 且配了 embed_model 才算真语义模型，
        # 否则本来就是 hash（此时用 hash 向量不算降级，签名一致即可正常检索）。
        if backend in ("provider", "auto"):
            self._intended = "provider" if self.cfg.embed_model else "hash"
        else:
            self._intended = backend
        if backend in ("provider", "auto"):
            # 「自定义配置」：仅当显式指定了 embedding.embed_model 才尝试网络 embedding；
            # 未配置 embed_model（即未自定义）时，直接降级 hash，绝不发起网络请求。
            if not self.cfg.embed_model:
                if self.cfg.fallback_hash:
                    logger.info(
                        "未配置 embedding.embed_model（未自定义 embedding 模型），"
                        "自动降级为 hash 向量，不发起任何网络请求。如需语义 embedding，"
                        "请在配置中指定 embedding.provider 与 embedding.embed_model。")
                    self._backend = "hash"
                    self._dim = self.cfg.dim
                    return
                raise RuntimeError(
                    "embedding.backend=provider 但未配置 embedding.embed_model，且 "
                    "fallback_hash=false。请配置支持 /embeddings 的模型名，或将 backend 改为 hash/local。")
            # 已自定义 embed_model：解析 Provider 并走网络 embeddings
            try:
                pc = self._resolve_embed_provider()
            except RuntimeError as e:
                # 指定了模型但解析不到 Provider：降级 hash 保证流程不中断
                logger.warning("未解析到可用于 embedding 的 Provider，降级为 hash 向量：%s", e)
                self._backend = "hash"
                self._dim = self.cfg.dim
                return
            self._provider_pc = pc
            self._backend = "provider"
            self._dim = self.cfg.dim  # 维度以实际返回为准，查询时自适应
            return
        # 显式后端
        for try_b in (backend, "hash"):
            if self._try_backend(try_b):
                return

    def _resolve_embed_provider(self) -> "object":
        """按配置解析用于 embedding 的 Provider。找不到则抛错。"""
        cfg = self.app_cfg
        if cfg is None:
            raise RuntimeError(
                "未注入模型配置（app_cfg），无法使用 provider 网络 embedding；"
                "请显式配置 embedding.backend 为 local / ollama / hash。"
            )
        # 1) 显式指定
        if self.cfg.provider:
            pc = cfg.providers.get(self.cfg.provider)
            if pc:
                return pc
        # 2) 跟随默认模型解析（与 LLM 同一套逻辑）
        if cfg.default_model:
            try:
                pc, _ = cfg.resolve_model(None)
                if pc:
                    return pc
            except RuntimeError:
                pass
        # 3) 退一步：任意「可用」的 Provider（enabled 且已配 Key 或本地端点），与 LLM 解析一致
        for p in cfg.usable_providers():
            return p
        raise RuntimeError(
            "未配置任何可用于 embedding 的模型 Provider。请在「模型管理」中配置"
            "支持 /embeddings 的模型（如通义、DeepSeek、Agnes 等），或显式设置"
            "embedding.backend 为 local / ollama / hash。"
        )

    def _try_backend(self, backend: str) -> bool:
        if backend == "local":
            try:
                from sentence_transformers import SentenceTransformer  # 本地模型，非境外SDK
                self._model = SentenceTransformer(self.cfg.model)
                self._dim = self._model.get_sentence_embedding_dimension()
                self._backend = "local"
                return True
            except Exception:
                return False
        if backend == "ollama":
            try:
                # 探测 ollama 可用性（仅当用户显式配置 backend=ollama 时）
                r = httpx.get(self.cfg.ollama_base + "/api/tags", timeout=3)
                if r.status_code == 200:
                    self._backend = "ollama"
                    self._dim = self.cfg.dim  # 维度以实际返回为准，查询时自适应
                    self._http = httpx.Client(timeout=30)
                    return True
            except Exception:
                return False
        if backend == "hash":
            self._backend = "hash"
            self._dim = self.cfg.dim
            return True
        return False

    @property
    def dim(self) -> int:
        self._lazy_init()
        return self._dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        self._lazy_init()
        if self._backend == "local":
            vecs = self._model.encode(texts, normalize_embeddings=True)
            self._last_kind = "local"
            return [v.tolist() for v in vecs]
        if self._backend == "provider":
            # 已确认该 Provider 网络 embedding 不可用时，直接走 hash，避免反复打网络
            if self._provider_failed:
                self._last_kind = "hash"
                return [self._hash_vec(t) for t in texts]
            try:
                out = self._embed_provider(texts)
                self._last_kind = "provider"
                return out
            except Exception as e:  # 网络/模型不可用：优雅降级，不让其拖垮上下文组装
                if self.cfg.fallback_hash:
                    logger.warning(
                        "Embedding 走网络 Provider(%s) 失败，已降级为 hash 向量（知识库仍可检索，"
                        "但为确定性哈希、语义能力弱）。原因：%s。"
                        "建议：配置支持 /embeddings 的模型并在 embedding.embed_model 中指定，"
                        "或显式设置 embedding.backend=hash/local。",
                        getattr(self._provider_pc, "name", "?"), e)
                    self._provider_failed = True
                    self._last_kind = "hash"
                    return [self._hash_vec(t) for t in texts]
                raise
        if self._backend == "ollama":
            out = []
            for t in texts:
                r = self._http.post(
                    self.cfg.ollama_base + "/api/embeddings",
                    json={"model": self.cfg.ollama_model, "prompt": t},
                )
                r.raise_for_status()
                out.append(r.json()["embedding"])
            self._last_kind = "ollama"
            return out
        # hash 降级：字符 n-gram 哈希到 dim 维，再做 L2 归一化
        self._last_kind = "hash"
        return [self._hash_vec(t) for t in texts]

    # --------------------- 降级隔离：向量签名 ---------------------
    @property
    def degraded(self) -> bool:
        """上一批向量是否为「降级产物」（本应真语义模型，实际退回 hash）。"""
        return bool(self._last_kind and self._intended
                    and self._last_kind != self._intended)

    @property
    def unconfigured(self) -> bool:
        """本应走 provider/auto 语义后端，但未配置 embed_model —— 静默降级陷阱。

        这是「未配置 Embedding 模型」的根因：backend=provider/auto 且 embed_model
        为空时，系统直接把 _intended 设为 hash，于是 degraded 永远为 False，
        全程静默用 hash 伪向量冒充语义检索，导致 RAG/记忆检索毫无语义能力。
        """
        self._lazy_init()
        return self.cfg.backend in ("provider", "auto") and not self.cfg.embed_model

    @property
    def semantic_available(self) -> bool:
        """当前是否真正使用语义 embedding（local/ollama/已配置模型的 provider）。

        仅当此值为 True 时，向量检索才具备语义能力；否则应把检索权重交给全文检索。
        """
        self._lazy_init()
        return self._intended not in (None, "hash")

    def _signature(self, kind: Optional[str], dim: int) -> str:
        """向量空间签名：不同签名的向量**不可互相比较**，必须隔离。"""
        kind = kind or self._backend or "hash"
        if kind == "hash":
            return f"hash:{dim}"
        if kind == "local":
            return f"local:{self.cfg.model}:{dim}"
        if kind == "ollama":
            return f"ollama:{self.cfg.ollama_model}:{dim}"
        pc_name = getattr(getattr(self, "_provider_pc", None), "name", "?")
        return f"provider:{pc_name}:{self.cfg.embed_model}:{dim}"

    @property
    def signature(self) -> str:
        """当前**预期**的向量空间签名（用于展示与陈旧向量统计）。"""
        self._lazy_init()
        return self._signature(self._intended, self._dim)

    def embed_tagged(self, texts: List[str]) -> tuple:
        """返回 (向量, 本批实际签名, 是否降级)。

        入库/检索一律走本方法：签名随**实际使用的后端**走，Provider 临时抖动
        产生的 hash 伪向量会被打上 `hash:<dim>`，与真语义向量天然隔离，
        既不会污染检索结果，也不会因维度不同在余弦计算时抛异常。
        """
        vecs = self.embed(texts)
        dim = len(vecs[0]) if vecs else self._dim
        return vecs, self._signature(self._last_kind, dim), self.degraded

    def _embed_provider(self, texts: List[str]) -> List[List[float]]:
        """走配置的模型 Provider 的 /embeddings（网络）。与 LLM 共用解析链。

        优化：OpenAI 兼容 /embeddings 接受 input 为字符串数组，一次性批量请求，
        把 N 段文本（知识库切片常达数百）的 N 次串行 HTTP 合并为 1 次，显著降低
        入库延迟。批量失败（个别 Provider 不支持批量输入）时退化为逐条请求以保兼容。
        """
        pc = self._provider_pc
        url = pc.base_url.rstrip("/") + "/embeddings"
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        # 进入本分支时 embed_model 必然已配置（_lazy_init 已保证）
        model = self.cfg.embed_model
        if self._http is None:
            self._http = httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=10.0))
        # 1) 批量请求
        try:
            r = self._http.post(url, json={"model": model, "input": list(texts)}, headers=headers)
            r.raise_for_status()
            data = r.json().get("data") or []
            if len(data) != len(texts):
                raise ValueError(
                    f"embedding 返回条数 {len(data)} 与输入 {len(texts)} 不符")
            out = [d["embedding"] for d in data]
            self._dim = len(out[0]) if out else self._dim
            return out
        except Exception as e:
            logger.warning(
                "批量 embedding 失败，退化为逐条请求：%s", e)
        # 2) 逐条退化（兼容不支持批量 input 的端点）
        out = []
        for t in texts:
            r = self._http.post(url, json={"model": model, "input": t}, headers=headers)
            r.raise_for_status()
            out.append(r.json()["data"][0]["embedding"])
        self._dim = len(out[0]) if out else self._dim
        return out

    def _hash_vec(self, text: str) -> List[float]:
        dim = self._dim
        vec = np.zeros(dim, dtype=np.float32)
        # 中英文混合 n-gram（2~3）
        grams = []
        for n in (2, 3):
            for i in range(len(text) - n + 1):
                grams.append(text[i:i + n])
        if not grams:
            grams = [text or " "]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h >> 1) & 1 else -1.0
            vec[idx] += sign
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()
