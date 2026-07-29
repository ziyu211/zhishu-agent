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

    def _lazy_init(self):
        if self._backend is not None:
            return
        backend = self.cfg.backend
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
            return [v.tolist() for v in vecs]
        if self._backend == "provider":
            # 已确认该 Provider 网络 embedding 不可用时，直接走 hash，避免反复打网络
            if self._provider_failed:
                return [self._hash_vec(t) for t in texts]
            try:
                return self._embed_provider(texts)
            except Exception as e:  # 网络/模型不可用：优雅降级，不让其拖垮上下文组装
                if self.cfg.fallback_hash:
                    logger.warning(
                        "Embedding 走网络 Provider(%s) 失败，已降级为 hash 向量（知识库仍可检索，"
                        "但为确定性哈希、语义能力弱）。原因：%s。"
                        "建议：配置支持 /embeddings 的模型并在 embedding.embed_model 中指定，"
                        "或显式设置 embedding.backend=hash/local。",
                        getattr(self._provider_pc, "name", "?"), e)
                    self._provider_failed = True
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
            return out
        # hash 降级：字符 n-gram 哈希到 dim 维，再做 L2 归一化
        return [self._hash_vec(t) for t in texts]

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
