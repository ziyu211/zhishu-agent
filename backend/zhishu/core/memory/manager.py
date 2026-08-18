"""智枢智能体 —— 记忆管理器（对标 Hermes `agent/memory_manager.py`）。

  编排记忆 provider：内置会话记忆（SQLite，由 Agent 直接管理历史）始终存在；
  至多可挂一个外部 provider（向量抽取式长期记忆，opt-in）。强制「至多一个外部
  provider」，避免 schema 膨胀（与 Hermes 约束一致）。

  相对原智枢实现对齐 Hermes 的增强：
    * 后台非阻塞同步（ThreadPoolExecutor 守护线程，单 worker 串行写入；
      外部 provider 的 LLM 抽取通过事件循环 run_coroutine_threadsafe 调用，绝不阻塞主流程）；
    * 上下文围栏（sanitize_context / build_memory_context_block / StreamingContextScrubber）；
    * trivial 门控（is_trivial_prompt）+ 检索问句改写（query_rewrite）；
    * 会话生命周期钩子转发（on_session_end / on_pre_compress / on_session_switch / on_delegation）；
    * 工具路由（get_all_tool_schemas / has_tool / handle_tool_call / normalize_tool_schema）；
    * shutdown 优雅收敛（先有界排空后台写入，再逆序关停 provider）。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional

from ..config import ZhishuConfig
from .provider import MemoryProvider, is_trivial_prompt
from .sqlite_provider import SQLiteMemoryProvider, MemoryStore
from .vector_provider import VectorMemoryProvider
from .query_rewrite import rewrite_memory_query

logger = logging.getLogger("zhishu.memory")

# 外部 provider 预召回的阻塞上限（秒）：wedged provider 绝不可无限期卡住主流程。
_EXTERNAL_PREFETCH_TIMEOUT_S = 8.0
# shutdown 等待在途后台同步/召回排空的上限（秒）：超过则放弃并通报。
_SYNC_DRAIN_TIMEOUT_S = 5.0


def normalize_tool_schema(schema: Any) -> Optional[Dict[str, Any]]:
    """返回带可解析顶层 ``name`` 的函数工具 dict。

    记忆 provider 经 get_tool_schemas() 暴露工具声明，期望形态是裸函数 schema
    （``{"name": ..., "description": ..., "parameters": ...}``），由调用方包成
    ``{"type": "function", "function": schema}``。个别 provider 返回的是已包好的
    OpenAI 工具形态（``{"type": "function", "function": {"name": ...}}``），二次
    包裹会产生无顶层 name 的嵌套结构，严格网关（DeepSeek 等）会整体 400。本函数
    两种形态归一为裸函数 schema，无法解析 name 时返回 None 以便调用方跳过并告警。
    """
    if not isinstance(schema, dict):
        return None
    if schema.get("type") == "function" and isinstance(schema.get("function"), dict):
        schema = schema["function"]
        if not isinstance(schema, dict):
            return None
    name = schema.get("name", "")
    if not name or not isinstance(name, str):
        return None
    return schema


# ---------------------------------------------------------------------------
# 上下文围栏（对标 Hermes）
# ---------------------------------------------------------------------------

_FENCE_TAG_RE = re.compile(r'</?\s*memory-context\s*>', re.IGNORECASE)
_INTERNAL_CONTEXT_RE = re.compile(
    r'<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>',
    re.IGNORECASE,
)
_INTERNAL_NOTE_RE = re.compile(
    r'\[System note:\s*The following is recalled memory context,\s*NOT new user input\.\s*Treat as (?:informational background data|authoritative reference data[^\]]*)\.\]\s*',
    re.IGNORECASE,
)


def sanitize_context(text: str) -> str:
    """剥离 provider 输出里的围栏标签、注入上下文块与系统提示备注。"""
    if not text:
        return ""
    text = _INTERNAL_CONTEXT_RE.sub('', text)
    text = _INTERNAL_NOTE_RE.sub('', text)
    text = _FENCE_TAG_RE.sub('', text)
    return text


class StreamingContextScrubber:
    """流式文本的状态化清洗器，可跨 chunk 边界处理被切开的 memory-context 片段。

    单次 sanitize_context 正则无法跨 delta 拼接；本清洗器以小型状态机跨 delta
    运行，拦下跨边界的开放/闭合标签之间的全部内容（含系统备注行）。
    """

    _OPEN_TAG = "<memory-context>"
    _CLOSE_TAG = "</memory-context>"

    def __init__(self) -> None:
        self._in_span: bool = False
        self._buf: str = ""
        self._at_block_boundary: bool = True

    def reset(self) -> None:
        self._in_span = False
        self._buf = ""
        self._at_block_boundary = True

    def feed(self, text: str) -> str:
        if not text:
            return ""
        buf = self._buf + text
        self._buf = ""
        out: list[str] = []
        while buf:
            if self._in_span:
                idx = buf.lower().find(self._CLOSE_TAG)
                if idx == -1:
                    held = self._max_partial_suffix(buf, self._CLOSE_TAG)
                    self._buf = buf[-held:] if held else ""
                    return "".join(out)
                buf = buf[idx + len(self._CLOSE_TAG):]
                self._in_span = False
            else:
                idx = self._find_boundary_open_tag(buf)
                if idx == -1:
                    held = (
                        self._max_pending_open_suffix(buf)
                        or self._max_partial_suffix(buf, self._OPEN_TAG)
                    )
                    if held:
                        self._append_visible(out, buf[:-held])
                        self._buf = buf[-held:]
                    else:
                        self._append_visible(out, buf)
                    return "".join(out)
                if idx > 0:
                    self._append_visible(out, buf[:idx])
                buf = buf[idx + len(self._OPEN_TAG):]
                self._in_span = True
        return "".join(out)

    def flush(self) -> str:
        if self._in_span:
            self._buf = ""
            self._in_span = False
            return ""
        tail = self._buf
        self._buf = ""
        return tail

    @staticmethod
    def _max_partial_suffix(buf: str, tag: str) -> int:
        tag_lower = tag.lower()
        buf_lower = buf.lower()
        max_check = min(len(buf_lower), len(tag_lower) - 1)
        for i in range(max_check, 0, -1):
            if tag_lower.startswith(buf_lower[-i:]):
                return i
        return 0

    def _find_boundary_open_tag(self, buf: str) -> int:
        buf_lower = buf.lower()
        search_start = 0
        while True:
            idx = buf_lower.find(self._OPEN_TAG, search_start)
            if idx == -1:
                return -1
            if self._is_block_boundary(buf, idx) and self._has_block_opener_suffix(buf, idx):
                return idx
            search_start = idx + 1

    def _max_pending_open_suffix(self, buf: str) -> int:
        if not buf.lower().endswith(self._OPEN_TAG):
            return 0
        idx = len(buf) - len(self._OPEN_TAG)
        if not self._is_block_boundary(buf, idx):
            return 0
        return len(self._OPEN_TAG)

    def _has_block_opener_suffix(self, buf: str, idx: int) -> bool:
        after_idx = idx + len(self._OPEN_TAG)
        if after_idx >= len(buf):
            return False
        return buf[after_idx] in "\r\n"

    def _is_block_boundary(self, buf: str, idx: int) -> bool:
        if idx == 0:
            return self._at_block_boundary
        preceding = buf[:idx]
        last_newline = preceding.rfind("\n")
        if last_newline == -1:
            return self._at_block_boundary and preceding.strip() == ""
        return preceding[last_newline + 1:].strip() == ""

    def _append_visible(self, out: list[str], text: str) -> None:
        if not text:
            return
        out.append(text)
        self._update_block_boundary(text)

    def _update_block_boundary(self, text: str) -> None:
        last_newline = text.rfind("\n")
        if last_newline != -1:
            self._at_block_boundary = text[last_newline + 1:].strip() == ""
        else:
            self._at_block_boundary = self._at_block_boundary and text.strip() == ""


def build_memory_context_block(raw_context: str) -> str:
    """把预召回记忆包进带系统备注的围栏块。"""
    if not raw_context or not raw_context.strip():
        return ""
    clean = sanitize_context(raw_context)
    if clean != raw_context:
        logger.warning("memory provider returned pre-wrapped context; stripped")
    return (
        "<memory-context>\n"
        "[System note: The following is recalled memory context, "
        "NOT new user input. Treat as authoritative reference data — "
        "this is the agent's persistent memory and should inform all responses.]\n\n"
        f"{clean}\n"
        "</memory-context>"
    )


def _provider_sync_accepts_messages(provider: MemoryProvider) -> bool:
    """返回 provider 的 sync_turn 是否接受 messages 关键字。"""
    try:
        signature = inspect.signature(provider.sync_turn)
    except (TypeError, ValueError):
        return True
    params = list(signature.parameters.values())
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
        return True
    return "messages" in signature.parameters


class MemoryManager:
    """编排内置 provider + 至多一个外部 provider。

    内置 provider 始终第一；仅允许一个非内置（外部）provider。任一 provider
    的失败绝不阻塞其它 provider。
    """

    def __init__(self, cfg: ZhishuConfig, data_dir: str, builtin_store: MemoryStore,
                 *, external_prefetch_timeout: Optional[float] = None) -> None:
        self.cfg = cfg
        self.data_dir = data_dir
        self.builtin_store = builtin_store
        self.builtin = SQLiteMemoryProvider(store=builtin_store)
        self._providers: List[MemoryProvider] = [self.builtin]
        self._tool_to_provider: Dict[str, MemoryProvider] = {}
        self._has_external: bool = False
        self._external: Optional[MemoryProvider] = None

        mem = getattr(cfg, "memory", None)
        self.query_rewrite_enabled: bool = getattr(mem, "query_rewrite_enabled", True)
        self.extraction_enabled: bool = getattr(mem, "extraction_enabled", True)

        self._external_prefetch_timeout = (
            _EXTERNAL_PREFETCH_TIMEOUT_S
            if external_prefetch_timeout is None
            else float(external_prefetch_timeout)
        )
        if self._external_prefetch_timeout <= 0:
            raise ValueError("external_prefetch_timeout must be positive")

        self._sync_executor: Optional[ThreadPoolExecutor] = None
        self._sync_executor_lock = threading.Lock()
        self._background_futures: Dict[Future, str] = {}
        self._shutting_down = False
        self._shutdown_drain_state: Dict[str, Any] = {
            "status": "not_started",
            "abandoned_writes": 0,
            "abandoned_prefetches": 0,
            "active_tasks": 0,
        }

        self._async_llm = None
        self._loop = None

        # 注册内置 provider 工具
        self._register_provider_tools(self.builtin)

        # 挂载外部向量（抽取式）provider（opt-in；失败优雅降级为 None）
        if getattr(mem, "vector_enabled", False):
            try:
                self._external = VectorMemoryProvider(
                    cfg, data_dir, getattr(mem, "vector_top_k", 5),
                    extraction_enabled=self.extraction_enabled,
                    extraction_interval=getattr(mem, "extraction_interval", 6),
                    extraction_model=getattr(mem, "extraction_model", None),
                )
                self._providers.append(self._external)
                self._register_provider_tools(self._external)
                self._has_external = True
            except Exception as e:
                logger.warning("外部向量记忆 provider 初始化失败，已降级为 None：%s", e)
                self._external = None

    # -- 注册 ----------------------------------------------------------------

    def _register_provider_tools(self, provider: MemoryProvider) -> None:
        for raw_schema in provider.get_tool_schemas():
            schema = normalize_tool_schema(raw_schema)
            if schema is None:
                continue
            tool_name = schema["name"]
            if tool_name and tool_name not in self._tool_to_provider:
                self._tool_to_provider[tool_name] = provider

    @property
    def providers(self) -> List[MemoryProvider]:
        return list(self._providers)

    def get_provider(self, name: str) -> Optional[MemoryProvider]:
        for p in self._providers:
            if p.name == name:
                return p
        return None

    # -- LLM 注入 ------------------------------------------------------------

    def set_llm(self, async_llm_callable: Callable) -> None:
        """注入 async callable(messages, model=None) -> str（供 query_rewrite 与抽取使用）。"""
        self._async_llm = async_llm_callable
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        for p in self._providers:
            try:
                p.set_llm(async_llm_callable, self._loop)
            except Exception:
                pass

    def _call_llm_sync(self, messages: Any, model: Optional[str] = None,
                       timeout: float = 60) -> str:
        """在后台线程里同步调用注入的 async LLM（经事件循环 run_coroutine_threadsafe）。"""
        if self._async_llm is None or self._loop is None:
            return ""
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._async_llm(messages, model=model), self._loop
            )
            return fut.result(timeout=timeout)
        except Exception as e:
            logger.debug("memory llm sync call failed: %s", e)
            return ""

    # -- 初始化 --------------------------------------------------------------

    async def initialize(self, session_id: str = "") -> None:
        for provider in self._providers:
            try:
                provider.initialize(session_id=session_id)
            except Exception as e:
                logger.warning("Memory provider '%s' initialize failed: %s", provider.name, e)

    # -- 系统提示 ------------------------------------------------------------

    def build_system_prompt(self) -> str:
        blocks = []
        for provider in self._providers:
            try:
                block = provider.system_prompt_block(owner=None)
                if block and block.strip():
                    blocks.append(block)
            except Exception as e:
                logger.warning("Memory provider '%s' system_prompt_block failed: %s",
                               provider.name, e)
        return "\n\n".join(blocks)

    # -- 预召回（trivial 门控 + query rewrite） -------------------------------

    def prefetch_all(self, query: str, *, owner: Optional[str] = None,
                     session_id: str = "") -> str:
        """收集所有 provider 的预召回上下文（带围栏）。无则空串。"""
        if is_trivial_prompt(query):
            return ""
        search_query = query
        if self.query_rewrite_enabled:
            try:
                rw = rewrite_memory_query(query, llm_call=lambda m: self._call_llm_sync(m))
                if rw:
                    search_query = rw
            except Exception as e:
                logger.debug("memory query rewrite skipped: %s", e)
        parts = []
        for provider in self._providers:
            try:
                result = self._prefetch_provider(provider, search_query,
                                                  owner=owner, session_id=session_id)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug("Memory provider '%s' prefetch failed (non-fatal): %s",
                             provider.name, e)
        if not parts:
            return ""
        return build_memory_context_block("\n\n".join(parts))

    def _prefetch_provider(self, provider: MemoryProvider, query: str, *,
                           owner: Optional[str] = None, session_id: str = "") -> str:
        if provider.name == "builtin":
            return provider.prefetch(query, owner=owner, session_id=session_id)
        # 外部 provider 在守护线程里跑，带超时；卡住则本轮跳过，绝不阻塞主流程。
        result_box: Dict[str, str] = {}
        error_box: Dict[str, Exception] = {}

        def _run() -> None:
            try:
                result_box["value"] = provider.prefetch(query, owner=owner,
                                                        session_id=session_id) or ""
            except Exception as exc:
                error_box["value"] = exc

        thread = threading.Thread(target=_run, daemon=True,
                                  name=f"memory-prefetch-{provider.name}")
        thread.start()
        thread.join(self._external_prefetch_timeout)
        if thread.is_alive():
            logger.warning(
                "Memory provider '%s' prefetch timed out after %.1fs; skipping it",
                provider.name, self._external_prefetch_timeout,
            )
            return ""
        if error_box:
            raise error_box["value"]
        return result_box.get("value", "")

    # -- 同步（后台非阻塞） --------------------------------------------------

    def schedule_sync(self, user_content: str, assistant_content: str = "",
                      *, owner: Optional[str] = None, session_id: str = "",
                      messages: Optional[List[Dict[str, Any]]] = None) -> None:
        """后台非阻塞地把一轮完成的对话同步给所有（非内置）provider。

        内置 provider 的会话流水由 Agent 直接经 MemoryStore 管理，这里跳过，
        避免重复落库。外部 provider（如向量抽取式）在单 worker 后台线程里串行写入，
        即使其内部调用 LLM 抽取也不会阻塞主流程。
        """
        providers = [p for p in self._providers if p.name != "builtin"]
        if not providers:
            return
        if not user_content or not user_content.strip():
            return

        def _run() -> None:
            for provider in providers:
                try:
                    if messages is not None and _provider_sync_accepts_messages(provider):
                        provider.sync_turn(
                            user_content, assistant_content,
                            owner=owner, session_id=session_id, messages=messages,
                        )
                    else:
                        provider.sync_turn(
                            user_content, assistant_content,
                            owner=owner, session_id=session_id,
                        )
                except Exception as e:
                    logger.warning("Memory provider '%s' sync_turn failed: %s",
                                   provider.name, e)

        self._submit_background(_run)

    # -- 后台调度 ------------------------------------------------------------

    def _submit_background(self, fn, *, kind: str = "write") -> None:
        executor = self._get_sync_executor()
        if executor is None:
            if self._shutting_down:
                logger.warning("Memory manager is shutting down; rejecting late %s task", kind)
                return
            try:
                fn()
            except Exception as e:
                logger.debug("Inline memory background task failed: %s", e)
            return
        try:
            with self._sync_executor_lock:
                if self._shutting_down:
                    logger.warning("Memory manager is shutting down; rejecting late %s task", kind)
                    return
                future = executor.submit(fn)
                self._background_futures[future] = kind
            future.add_done_callback(self._forget_background_future)
        except RuntimeError:
            if self._shutting_down:
                return
            try:
                fn()
            except Exception as e:
                logger.debug("Inline memory background task failed: %s", e)

    def _forget_background_future(self, future: Future) -> None:
        with self._sync_executor_lock:
            self._background_futures.pop(future, None)

    def _get_sync_executor(self) -> Optional[ThreadPoolExecutor]:
        if self._shutting_down:
            return None
        if self._sync_executor is not None:
            return self._sync_executor
        with self._sync_executor_lock:
            if self._shutting_down:
                return None
            if self._sync_executor is None:
                try:
                    # daemon_threads 在 Python 3.12+ 才支持；3.11 用普通线程，
                    # 进程退出前由 shutdown_all 排空，不会阻塞退出。
                    _exec_kwargs = {"max_workers": 1, "thread_name_prefix": "mem-sync"}
                    if sys.version_info >= (3, 12):
                        _exec_kwargs["daemon_threads"] = True
                    self._sync_executor = ThreadPoolExecutor(**_exec_kwargs)
                except Exception as e:
                    logger.warning("Failed to create memory sync executor: %s", e)
                    return None
            return self._sync_executor

    def flush_pending(self, timeout: Optional[float] = None) -> bool:
        """阻塞直到排队的同步/召回工作排空（测试与真实会话边界用）。"""
        executor = self._sync_executor
        if executor is None:
            return True
        try:
            fut = executor.submit(lambda: None)
        except RuntimeError:
            return True
        try:
            fut.result(timeout=timeout)
            return True
        except Exception:
            return False

    # -- 工具路由 ------------------------------------------------------------

    def get_all_tool_schemas(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        schemas = []
        seen = set()
        for provider in self._providers:
            try:
                for raw_schema in provider.get_tool_schemas(owner=owner):
                    schema = normalize_tool_schema(raw_schema)
                    if schema is None:
                        logger.warning(
                            "Memory provider '%s' returned a tool schema with no "
                            "resolvable name; skipping (%r)", provider.name, raw_schema,
                        )
                        continue
                    name = schema["name"]
                    if name not in seen:
                        schemas.append(schema)
                        seen.add(name)
            except Exception as e:
                logger.warning("Memory provider '%s' get_tool_schemas failed: %s",
                               provider.name, e)
        return schemas

    def get_all_tool_names(self) -> set:
        return set(self._tool_to_provider.keys())

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tool_to_provider

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        provider = self._tool_to_provider.get(tool_name)
        if provider is None:
            return f'{{"error": "No memory provider handles tool {tool_name!r}"}}'
        try:
            return provider.handle_tool_call(tool_name, args, **kwargs)
        except Exception as e:
            logger.error("Memory provider '%s' handle_tool_call(%s) failed: %s",
                         provider.name, tool_name, e)
            return f'{{"error": "Memory tool {tool_name!r} failed: {e}"}}'

    # -- 生命周期钩子 --------------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            try:
                provider.on_turn_start(turn_number, message, **kwargs)
            except Exception as e:
                logger.debug("Memory provider '%s' on_turn_start failed: %s",
                             provider.name, e)

    def on_session_end(self, messages: List[Dict[str, Any]], *,
                       owner: Optional[str] = None, session_id: str = "") -> None:
        """通知所有非内置 provider 会话结束（后台非阻塞执行抽取）。"""
        providers = [p for p in self._providers if p.name != "builtin"]
        if not providers:
            return
        snapshot = list(messages or [])

        def _run() -> None:
            for provider in providers:
                try:
                    provider.on_session_end(snapshot, owner=owner, session_id=session_id)
                except Exception as e:
                    logger.warning("Memory provider '%s' on_session_end failed: %s",
                                   provider.name, e)

        self._submit_background(_run, kind="session_end")

    def on_session_switch(self, new_session_id: str, *,
                          owner: Optional[str] = None, parent_session_id: str = "",
                          reset: bool = False, rewound: bool = False, **kwargs) -> None:
        if not new_session_id:
            return
        if rewound:
            kwargs["rewound"] = True
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            try:
                provider.on_session_switch(
                    new_session_id, owner=owner,
                    parent_session_id=parent_session_id, reset=reset, **kwargs,
                )
            except Exception as e:
                logger.debug("Memory provider '%s' on_session_switch failed: %s",
                             provider.name, e)

    def on_pre_compress(self, messages: List[Dict[str, Any]], *,
                        owner: Optional[str] = None, session_id: str = "") -> str:
        """通知所有非内置 provider 压缩前抽取；返回要纳入压缩摘要的文本。"""
        parts = []
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            try:
                result = provider.on_pre_compress(messages, owner=owner, session_id=session_id)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug("Memory provider '%s' on_pre_compress failed: %s",
                             provider.name, e)
        return "\n\n".join(parts)

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            try:
                provider.on_delegation(task, result, child_session_id=child_session_id, **kwargs)
            except Exception as e:
                logger.debug("Memory provider '%s' on_delegation failed: %s",
                             provider.name, e)

    def shutdown_all(self) -> None:
        """关停所有 provider（逆序）。先有界排空后台同步，再逆序关停。"""
        self._drain_sync_executor()
        for provider in reversed(self._providers):
            try:
                provider.shutdown()
            except Exception as e:
                logger.warning("Memory provider '%s' shutdown failed: %s", provider.name, e)

    @property
    def shutdown_drain_state(self) -> Dict[str, Any]:
        with self._sync_executor_lock:
            return dict(self._shutdown_drain_state)

    def _drain_sync_executor(self) -> None:
        with self._sync_executor_lock:
            self._shutting_down = True
            executor = self._sync_executor
            self._sync_executor = None
            tracked = dict(self._background_futures)
            self._shutdown_drain_state = {
                "status": "draining" if executor is not None else "drained",
                "abandoned_writes": 0,
                "abandoned_prefetches": 0,
                "active_tasks": sum(not future.done() for future in tracked),
            }
        if executor is None:
            return
        executor.shutdown(wait=False, cancel_futures=False)
        _, pending = wait(tuple(tracked), timeout=_SYNC_DRAIN_TIMEOUT_S)
        if not pending:
            with self._sync_executor_lock:
                self._shutdown_drain_state.update(status="drained", active_tasks=0)
            return
        abandoned_writes = 0
        abandoned_prefetches = 0
        active_tasks = 0
        for future in pending:
            kind = tracked[future]
            if future.cancel():
                if kind == "prefetch":
                    abandoned_prefetches += 1
                else:
                    abandoned_writes += 1
            else:
                active_tasks += 1
        with self._sync_executor_lock:
            self._shutdown_drain_state.update(
                status="timed_out",
                abandoned_writes=abandoned_writes,
                abandoned_prefetches=abandoned_prefetches,
                active_tasks=active_tasks,
            )
        logger.warning(
            "Memory shutdown drain timed out after %.2fs; abandoning %d queued write(s) "
            "and %d queued prefetch(es); %d active task(s) remain detached",
            _SYNC_DRAIN_TIMEOUT_S, abandoned_writes, abandoned_prefetches, active_tasks,
        )

    # -- 兼容旧接口（vector 可观测性） ---------------------------------------

    @property
    def vector_enabled(self) -> bool:
        return self._external is not None

    def vector_stats(self, owner: Optional[str] = None) -> dict:
        if self._external is not None and hasattr(self._external, "stats"):
            out = self._external.stats(owner)
            out["enabled"] = True
            return out
        return {"enabled": False, "backend": None, "count": 0, "owner": owner or "*"}

    def vector_clear(self, owner: Optional[str] = None) -> int:
        if self._external is not None and hasattr(self._external, "clear"):
            return self._external.clear(owner)
        return 0
