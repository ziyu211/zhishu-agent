"""智枢智能体 —— 向量抽取式长期记忆 Provider（对标 Hermes 外部 MemoryProvider + 抽取能力）。

  跨会话语义召回 + LLM 结构化抽取：
    * 原始写入（raw）：把每轮较长对话写入长期记忆后端，保留 verbatim 召回；
    * 结构化抽取（extraction，新增）：按 extract_interval 周期、会话结束、压缩前，
      用 LLM 从缓冲对话中抽取「关于用户的长期事实」入库，做到 Hermes 有而智枢
      此前缺失的「记忆抽取」能力，让长期记忆是干净的 facts 而非杂乱原始对话。

  后端可插拔（builtin / mem0 等），由 cfg.memory.backend 决定，默认 builtin（零回归）。

  默认关闭（cfg.memory.vector_enabled），零回归风险。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from ..config import ZhishuConfig
from .provider import MemoryProvider
from .backends import create_memory_backend, MemoryBackend

logger = logging.getLogger("zhishu.memory")

_EXTRACT_SYSTEM_PROMPT = """你是一个长期记忆抽取器。请从下面的对话中抽取对「用户」长期有用的结构化事实，用于日后跨会话回忆。

要求：
- 每行一条事实，简洁陈述（不超过 40 字），不要编号、不要解释、不要重复。
- 只抽取明确陈述的内容：用户偏好/身份/职业、正在进行的项目/任务、已做的决策、约束条件、待办事项、关键人物或实体。
- 不要抽取寒暄、客套、或无法确定真伪的猜测。
- 若没有值得长期记住的事实，只输出一个空行。

对话：
"""


class VectorMemoryProvider(MemoryProvider):
    name = "vector"

    def __init__(self, cfg: ZhishuConfig, data_dir: str, top_k: int = 5,
                 *, extraction_enabled: bool = True, extraction_interval: int = 6,
                 extraction_model: Optional[str] = None) -> None:
        self.top_k = top_k
        self.extraction_enabled = extraction_enabled
        self.extraction_interval = max(1, int(extraction_interval))
        self.extraction_model = extraction_model
        # 向量化完全委托给可插拔记忆后端（默认 builtin，可选 mem0 等）
        self.backend: MemoryBackend = create_memory_backend(cfg, data_dir)

        # 抽取状态（由 set_llm 注入 LLM 调用）
        self._async_llm: Optional[Callable] = None
        self._loop = None
        # 按 owner 缓冲待抽取对话片段；达到 extract_interval 触发一次抽取
        self._buffers: Dict[str, List[str]] = {}
        # 最近一次抽取出的 facts 文本（按 owner），供 on_pre_compress 回填压缩摘要
        self._last_extract: Dict[str, str] = {}

    # -- 生命周期 ------------------------------------------------------------

    def is_available(self) -> bool:
        return self.backend is not None

    def initialize(self, session_id: str = "", **kwargs) -> None:
        try:
            self.backend.initialize()
        except Exception:
            pass

    def set_llm(self, async_llm_callable: Callable, loop=None) -> None:
        self._async_llm = async_llm_callable
        self._loop = loop

    def _call_llm_sync(self, messages: Any, model: Optional[str] = None,
                       timeout: float = 60) -> str:
        if self._async_llm is None or self._loop is None:
            return ""
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._async_llm(messages, model=model), self._loop
            )
            return fut.result(timeout=timeout)
        except Exception as e:
            logger.debug("vector memory extraction llm call failed: %s", e)
            return ""

    # -- 工具 ----------------------------------------------------------------

    def get_tool_schemas(self, owner: Optional[str] = None) -> list:
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        raise NotImplementedError(f"vector provider 不处理工具 {tool_name}")

    # -- 预召回 --------------------------------------------------------------

    def prefetch(self, query: str, *, owner: Optional[str] = None,
                 session_id: str = "") -> str:
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

    # -- 同步（raw 写入 + 缓冲抽取） -----------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str = "", *,
                  owner: Optional[str] = None, session_id: str = "",
                  messages: Optional[List[Dict[str, Any]]] = None) -> None:
        owner_key = owner or "anon"
        # 1) raw 写入：较长的一方入库（保留 verbatim 召回）
        raw = (assistant_content or "") if len(assistant_content or "") >= len(user_content or "") else (user_content or "")
        if raw and len(raw) >= 12:
            try:
                self.backend.add(owner, raw, meta={
                    "title": f"mem_{owner_key}",
                    "source": f"mem_{owner_key}",
                })
            except Exception:
                pass

        # 2) 缓冲用于结构化抽取
        if self.extraction_enabled:
            buf = self._buffers.setdefault(owner_key, [])
            if user_content:
                buf.append(f"用户：{user_content}")
            if assistant_content:
                buf.append(f"助手：{assistant_content}")
            if len(buf) >= self.extraction_interval * 2:
                self._extract_from_buffer(owner_key)

    def _extract_from_buffer(self, owner_key: str) -> None:
        buf = self._buffers.get(owner_key)
        if not buf:
            return
        transcript = "\n".join(buf)[-6000:]
        self._buffers[owner_key] = []  # 清空已处理缓冲
        facts = self._extract_facts(transcript, owner_key)
        if facts:
            self._last_extract[owner_key] = "\n".join(f"- {f}" for f in facts)
            for f in facts:
                try:
                    self.backend.add(owner_key, f, meta={
                        "title": f"抽取·{owner_key}",
                        "source": "extraction",
                    })
                except Exception:
                    pass

    def _extract_facts(self, transcript: str, owner_key: str) -> List[str]:
        if not self._async_llm:
            return []
        try:
            resp = self._call_llm_sync(
                [{"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                 {"role": "user", "content": transcript}],
                model=self.extraction_model,
            )
        except Exception as e:
            logger.debug("memory extraction failed: %s", e)
            return []
        if not resp:
            return []
        facts: List[str] = []
        for line in resp.splitlines():
            line = line.strip()
            if not line:
                continue
            # 去掉 - / * / 数字编号等前缀
            line = line.lstrip("-*0123456789.、）。) ")
            line = line.strip()
            if line:
                facts.append(line)
        return facts

    # -- 生命周期钩子 --------------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]], *,
                       owner: Optional[str] = None, session_id: str = "") -> None:
        """会话结束：从完整对话抽取结构化事实入库。"""
        if not self.extraction_enabled:
            return
        transcript = "\n".join(
            f"{m.get('role', '?')}：{(m.get('content') or '')[:1500]}"
            for m in (messages or [])
            if isinstance(m, dict)
        )[:6000]
        if not transcript.strip():
            return
        owner_key = owner or "anon"
        facts = self._extract_facts(transcript, owner_key)
        if facts:
            self._last_extract[owner_key] = "\n".join(f"- {f}" for f in facts)
            for f in facts:
                try:
                    self.backend.add(owner, f, meta={
                        "title": f"抽取·{owner_key}",
                        "source": "extraction",
                    })
                except Exception:
                    pass

    def on_pre_compress(self, messages: List[Dict[str, Any]], *,
                        owner: Optional[str] = None, session_id: str = "") -> str:
        """压缩前返回最近一次抽取的事实，使压缩摘要保留这些洞察。"""
        owner_key = owner or "anon"
        last = self._last_extract.get(owner_key)
        if last:
            return "【长期记忆·抽取摘要（压缩前保留）】\n" + last
        return ""

    def shutdown(self) -> None:
        return None

    # -- 可观测性（兼容旧接口） ----------------------------------------------

    def stats(self, owner: Optional[str] = None) -> dict:
        if self.backend:
            return self.backend.stats(owner)
        return {"backend": None, "count": 0, "owner": owner or "*"}

    def clear(self, owner: Optional[str] = None) -> int:
        self._buffers.pop(owner or "anon", None)
        self._last_extract.pop(owner or "anon", None)
        if self.backend:
            return self.backend.clear(owner)
        return 0
