"""智枢智能体 —— 上下文引擎（对标 Hermes `agent/context_engine.py`）。

  可插拔的上下文管理/压缩策略：
    * NoOpContextEngine    —— 默认，不做压缩（与重构前行为一致）。
    * CompressionContextEngine —— 当历史轮数超过阈值，用 LLM 把较早的对话
                                  压缩为一段摘要，保护上下文窗口。

  通过 cfg.agent.compression_enabled 开启（默认关闭，零回归风险）。
  注意：compress_history 为异步方法，需在事件循环中 await（历史压缩会调用 LLM）。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Optional

from ..config import ZhishuConfig
from ..memory import MemoryStore
from ..providers.client import LLMClient


class ContextEngine(ABC):
    @abstractmethod
    async def compress_history(self, session: str, history: list[dict]) -> list[dict]:
        """返回压缩后的历史消息列表。"""


class NoOpContextEngine(ContextEngine):
    async def compress_history(self, session: str, history: list[dict]) -> list[dict]:
        return history


class CompressionContextEngine(ContextEngine):
    def __init__(self, cfg: ZhishuConfig, llm: LLMClient,
                 threshold: int = 24, keep_recent: int = 8):
        self.cfg = cfg
        self.llm = llm
        self.threshold = threshold
        self.keep_recent = keep_recent

    async def compress_history(self, session: str, history: list[dict]) -> list[dict]:
        if len(history) <= self.threshold:
            return history
        # 前 (len - keep_recent) 轮压缩为摘要，保留最近 keep_recent 轮原样
        early = history[: len(history) - self.keep_recent]
        recent = history[len(history) - self.keep_recent:]
        summary = await self._summarize(early)
        compressed = [{"role": "system", "content": f"[早期对话摘要]\n{summary}"}]
        return compressed + recent

    async def _summarize(self, early: list[dict]) -> str:
        """用 LLM 把多轮历史压缩为简洁中文摘要（失败则降级为占位文本，不影响主流程）。"""
        try:
            transcript = "\n".join(
                f"{m.get('role', '?'):}：{(m.get('content') or '')[:1500]}"
                for m in early
            )[:6000]
            resp = await self.llm.chat(
                [{"role": "system",
                  "content": "你是对话压缩器。把下面的多轮对话压缩成一段简洁中文摘要，"
                             "保留关键结论、决策与待办，不超过 300 字。"},
                 {"role": "user", "content": transcript}],
                model=self.cfg.default_model,
            )
            return (resp["choices"][0]["message"].get("content", "") or "")[:2000]
        except Exception:
            return "（历史对话较长，已折叠为摘要）"

    @staticmethod
    async def compress_tool_result(text: str, llm: LLMClient, cfg: ZhishuConfig,
                                   max_chars: int = 4000) -> str:
        """单条工具结果过长时，用 LLM 摘要为要点，降低单轮上下文占用（缓解 MAX_STEPS 耗尽）。

        仅当文本超过 max_chars 时触发；异常时原样返回，保证不丢信息。
        """
        if not text or len(text) <= max_chars:
            return text
        try:
            resp = await llm.chat(
                [{"role": "system",
                  "content": "你是工具结果压缩器。把下面这段较长的工具输出提炼为关键要点"
                             "（保留数字、结论、路径、错误原因），用中文分条列出，不超过 400 字。"},
                 {"role": "user", "content": text[:8000]}],
                model=cfg.default_model,
            )
            summary = resp["choices"][0]["message"].get("content", "") or ""
            return f"[工具结果已压缩为摘要]\n{summary[:1500]}\n\n（原始长度 {len(text)} 字符）"
        except Exception:
            return text


def build_context_engine(cfg: ZhishuConfig, llm: Optional[LLMClient] = None) -> ContextEngine:
    """按配置构建上下文引擎；未开启压缩则返回 NoOp。"""
    if getattr(cfg.agent, "compression_enabled", False) and llm is not None:
        return CompressionContextEngine(
            cfg, llm,
            threshold=getattr(cfg.agent, "compression_threshold", 24),
            keep_recent=getattr(cfg.agent, "compression_keep_recent", 8),
        )
    return NoOpContextEngine()
