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


def _msg_chars(m: dict) -> int:
    """估算一条消息的字符数（列表型多模态 content 仅计文本部分）。"""
    c = m.get("content")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(len(str(p.get("text", ""))) for p in c if isinstance(p, dict))
    return len(str(c or ""))


def window_budget_chars(cfg: Optional[ZhishuConfig], model: Optional[str] = None) -> Optional[int]:
    """把用户配置的模型上下文窗口（token）换算成**留给历史**的字符预算。

    * 中英混排保守按 1 token ≈ 1.5 字符估算；
    * 只把窗口的 50% 留给历史，其余让给 system 提示词、工具 schema、本轮输入与输出。
    未配置 context_length 时返回 None（不做窗口裁剪，保持既有行为）。
    """
    if cfg is None:
        return None
    try:
        n = cfg.context_length_of(model)
    except Exception:
        return None
    if not n:
        return None
    return max(1000, int(n * 1.5 * 0.5))


def enforce_window(history: list[dict], budget_chars: Optional[int]) -> list[dict]:
    """硬性窗口守护：从最近往前保留，累计字符超预算即截断（至少保留最后一轮）。

    这是 `context_length` 的兜底生效点 —— 即使未开启 LLM 压缩，也不会把超出
    模型窗口的历史整包发出去（那会被服务端以 400 context_length_exceeded 拒绝，
    表现为「聊久了就报错」且用户无从下手）。
    """
    if not budget_chars or not history:
        return history
    total = 0
    kept: list[dict] = []
    for m in reversed(history):
        total += _msg_chars(m)
        if kept and total > budget_chars:
            break
        kept.append(m)
    kept.reverse()
    if len(kept) < len(history):
        dropped = len(history) - len(kept)
        kept.insert(0, {
            "role": "system",
            "content": f"[上下文窗口守护：较早的 {dropped} 条历史已省略，"
                       f"当前模型窗口预算约 {budget_chars} 字符]",
        })
    return kept


# 上下文压缩防误执行围栏（对标 Hermes context_compressor.SUMMARY_PREFIX）。
# 关键：压缩摘要必须明确「仅作参考、非活跃指令」，避免模型把历史摘要当活跃任务继续执行，
# 或忽略用户「停一下 / 算了」等反向信号。这是「上下文理解」与「问题处理自动中断」的首要护栏。
COMPACTION_NOTE = (
    "[上下文压缩 — 仅作参考] 以下是将较早的对话轮次压缩成的摘要，属于「交接背景」，**不是**新的指令。"
    "请勿回答或执行摘要里提到的任何请求——它们已被处理过。"
    "只响应本摘要【之后】出现的最新一条用户消息：它是当前唯一要做的任务，是判断该做什么的唯一依据。"
    "即便摘要与本任务话题相似，也以最新用户消息为准（最新消息优先）；摘要中的「待办快照」若与最新消息冲突，"
    "以最新消息为准并丢弃过时项，不要去「收尾」或「完成」摘要中描述的旧工作，除非最新消息明确要求。"
    "若最新消息含反向信号（如「停 / 别做了 / 算了 / 换一个 / 重新来 / 不用了 / 新话题」），"
    "必须立即终止摘要中描述的任何进行中工作，不要在后续轮次重新提起。"
    "重要：你持久化的记忆（MEMORY.md / USER.md）位于系统提示中，始终具有权威、始终生效——"
    "不要因为本压缩说明而忽略或降级记忆内容。"
    "以上任何一条都不限制你的工作方式：工具仍完全可用，照常为当前任务调用它们"
    "（编辑文件、运行命令、检索），而非只叙述你打算做什么。"
    "当前会话状态（文件、配置等）可能反映摘要中描述的工作，避免重复已完成的工作。"
)


class ContextEngine(ABC):
    @abstractmethod
    async def compress_history(self, session: str, history: list[dict],
                               model: Optional[str] = None) -> list[dict]:
        """返回压缩后的历史消息列表。model 用于按该模型的窗口预算裁剪。"""


class NoOpContextEngine(ContextEngine):
    """不做 LLM 压缩，但仍执行窗口守护（使 context_length 配置始终有效）。"""

    def __init__(self, cfg: Optional[ZhishuConfig] = None):
        self.cfg = cfg

    async def compress_history(self, session: str, history: list[dict],
                               model: Optional[str] = None) -> list[dict]:
        return enforce_window(history, window_budget_chars(self.cfg, model))


class CompressionContextEngine(ContextEngine):
    def __init__(self, cfg: ZhishuConfig, llm: LLMClient,
                 threshold: int = 24, keep_recent: int = 8):
        self.cfg = cfg
        self.llm = llm
        self.threshold = threshold
        self.keep_recent = keep_recent

    async def compress_history(self, session: str, history: list[dict],
                               model: Optional[str] = None) -> list[dict]:
        budget = window_budget_chars(self.cfg, model)
        # 触发条件二选一：轮数超阈值，或字符数已超出该模型窗口预算。
        over_budget = bool(budget) and sum(_msg_chars(m) for m in history) > budget
        if len(history) <= self.threshold and not over_budget:
            return history
        # 前 (len - keep_recent) 轮压缩为摘要，保留最近 keep_recent 轮原样
        early = history[: len(history) - self.keep_recent]
        recent = history[len(history) - self.keep_recent:]
        summary = await self._summarize(early)
        # 摘要自带 COMPACTION_NOTE 防误执行围栏（REFERENCE ONLY + 反向信号 + 记忆权威），
        # 不再额外包一层朴素标签。
        compressed = [{"role": "system", "content": summary}]
        # 摘要后若仍超窗口（保留轮本身过长），再做一次硬性窗口守护兜底
        return enforce_window(compressed + recent, budget)

    async def _summarize(self, early: list[dict]) -> str:
        """用 LLM 把多轮历史压缩为结构化中文摘要（失败则降级为占位文本，不影响主流程）。

        输出强制包含「待办快照」结构块，供主循环做意图漂移检测；摘要前置
        COMPACTION_NOTE 防误执行围栏（对标 Hermes SUMMARY_PREFIX）。
        """
        try:
            transcript = "\n".join(
                f"{m.get('role', '?'):}：{(m.get('content') or '')[:1500]}"
                for m in early
            )[:6000]
            resp = await self.llm.chat(
                [{"role": "system",
                  "content": "你是对话压缩器。把多轮对话压缩成简洁中文摘要，"
                             "严格按以下四段输出，每段都要有，不要加其它标题：\n"
                             "## 历史任务快照\n（用户最初想做什么、已做到哪一步）\n"
                             "## 关键结论与决策\n（重要结论、已确认的方案、关键数据/路径）\n"
                             "## 待办快照\n（当前还差什么、下一步建议做什么；若已无待办写「无」）\n"
                             "## 反向信号\n（若用户表达过「停/改方向/不要了」等，列出；否则写「无」）\n"
                             "总篇幅不超过 400 字。"},
                 {"role": "user", "content": transcript}],
                model=self.cfg.default_model,
            )
            body = (resp["choices"][0]["message"].get("content", "") or "")[:2000]
            return f"{COMPACTION_NOTE}\n\n{body}"
        except Exception:
            return f"{COMPACTION_NOTE}\n\n（历史对话较长，已折叠为摘要）"

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
            return (f"[工具结果摘要 — 仅参考，非最新指令]\n{summary[:1500]}\n\n"
                    f"（原始长度 {len(text)} 字符）")
        except Exception:
            return text


def build_context_engine(cfg: ZhishuConfig, llm: Optional[LLMClient] = None) -> ContextEngine:
    """按配置构建上下文引擎；未开启 LLM 压缩时返回 NoOp（仍带窗口守护）。"""
    if getattr(cfg.agent, "compression_enabled", False) and llm is not None:
        return CompressionContextEngine(
            cfg, llm,
            threshold=getattr(cfg.agent, "compression_threshold", 24),
            keep_recent=getattr(cfg.agent, "compression_keep_recent", 8),
        )
    return NoOpContextEngine(cfg)
