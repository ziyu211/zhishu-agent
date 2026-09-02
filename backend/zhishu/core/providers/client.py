"""智枢智能体 —— 国产 LLM Provider 客户端（统一 chat/stream/embed + 回退链）。

  本文件对应 Hermes 的「LLM 客户端 + 回退链」职责，但把**协议格式转换**下沉到
  `ProviderTransport`（见 ./base.py、./registry.py），把**客户端构建**下沉到
  adapters（见 ./adapters.py）。本类只负责 HTTP 调用、流式解析、重试与多模态。

  当以 api_mode="moa" 构建时（即配置了 moa Provider），chat/stream 会路由到
  MoAClient（并行多个 reference agent + 聚合），实现「多智能体伪装成一个 LLM client」。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import threading
import time
from typing import Any, AsyncIterator, Optional

import httpx

from ..config import ZhishuConfig, ProviderConfig
from . import compat
from .registry import get_transport
from .prompt_cache import apply_prompt_cache
from .failover import classify_llm_error, AllProvidersFailedError, RATE_LIMIT


# OpenAI 兼容消息 / 工具结构（仅类型约定，运行时用 dict）
Message = dict
ToolSpec = dict


class _RetryableStatus(Exception):
    """内部异常：命中可重试 HTTP 状态码时抛出，携带响应对象。"""

    def __init__(self, resp: httpx.Response):
        self.resp = resp


def _extract_upstream_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                return (err.get("message") or str(err))[:300]
            if isinstance(err, str):
                return err[:300]
            if "message" in body:
                return str(body["message"])[:300]
    except Exception:
        pass
    try:
        txt = resp.text.strip()
        if txt:
            return txt[:300]
    except Exception:
        pass
    return ""


# 对话级瞬时故障重试：多智能体编排会在数秒内密集打同一个 Provider，很容易撞上
# 限流（429）。此前 429 会直接把该 Provider 判定为失败、遍历完回退链后抛
# 「所有 LLM Provider 均不可用」，表现为子智能体整体空输出。此处对**瞬时**故障
# （限流 / 网关抖动 / 超时）做指数退避重试，鉴权失败等永久性错误则立即失败不重试。
_TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504, 529}
_LLM_MAX_RETRIES = 5
_LLM_RETRY_BASE = 1.5
_LLM_RETRY_MAX = 15.0

# 整条回退链全部失败后的冷却：避免「所有 Provider 同时挂掉」时，agent 每个 step 都重跑
# 完整的 5 次退避重试链（极其缓慢且无效）。冷却期内再次调用则不再退避、直接快失败。
# 对标 Hermes 的 _FALLBACK_EXHAUSTED_COOLDOWN_S，但智枢把冷却语义下沉到客户端（更稳）。
_FALLBACK_EXHAUSTED_COOLDOWN_S = 6.0
_CHAIN_EXHAUSTED_AT: float = 0.0


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """尊重上游 Retry-After 响应头（秒 / HTTP-date 两种格式只解析前者）。"""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        v = resp.headers.get("Retry-After")
        return max(0.0, min(float(v), _LLM_RETRY_MAX)) if v else None
    except (TypeError, ValueError):
        return None


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                        httpx.RemoteProtocolError, httpx.PoolTimeout)):
        return True
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code in _TRANSIENT_STATUS


# ---------------------------------------------------------------------------
# 上下文超长（HTTP 400）自动裁剪重试
#
#   本地/内网模型（vLLM / Ollama 等）上下文窗口有限，把长文件、长工具结果整体
#   塞进对话时，会被服务端以 400 context_length_exceeded 拒绝。智枢虽有
#   `context_engine.enforce_window` 守护，但仅在「模型配置了 context_length」时
#   才生效；这里再补一层**调用级兜底**：检测到超长 400 时自动裁剪历史并重试，
#   不依赖任何配置，避免用户一来就 400 无能为力。
# ---------------------------------------------------------------------------
_CTX_TRUNCATE_RETRIES = 3
_CTX_OVERFLOW_HINTS = (
    "maximum context length", "context length", "too many tokens",
    "max_model_len", "prompt is too long", "token limit",
    "exceeds the context", "exceeds the maximum", "请求长度过长",
    "上下文长度", "超出上下文", "超出最大", "token 数量",
    # vLLM / SGLang / LMDeploy / MindIE / Ollama 等本地推理框架的变体措辞。
    # 例：vLLM 早期版本报错 "The input(130373tokens) is longer than the
    # model's ontert length (81920tokens)"（"ontert" 为该版本拼写错误），
    # 既不含 "context length" 也不含 "max_model_len"，必须靠 "longer than"
    # 兜底才能识别为超长并触发自动裁剪；否则会被误判为「Provider 不可用」。
    "longer than", "maximum length", "max context length",
    "context window", "sequence length",
    # 通用兜底：单独出现的 "too long" 也能命中（LMDeploy / Ollama 的
    # "input is too long" / "The input token length is too long" 等）。
    "too long",
)

# 结构化兜底正则：长度类名词 与 溢出类动词 在 40 字符内共现即判为超长。
# 不依赖具体英文单词顺序，跨 vLLM / SGLang / LMDeploy / MindIE / Ollama
# 各种措辞通吃，也覆盖未来新增推理框架，避免再出现「措辞没穷举到就误报
# Provider 不可用」的同类 bug。动词 / 名词各取其一即可，误报面很小
# （chat provider 的 400 体里几乎不会同时出现「长度名词 + 溢出动词」）。
# 注：名词不放 "max"，否则 "max retries exceeded" 这类会被误伤；
# "max context length" 等已由子串 hint 覆盖，无需靠正则名词 "max"。
_CTX_OVERFLOW_RE = re.compile(
    r"(?:context|window|sequence|prompt|input|token|length|model|"
    r"模型|上下文|序列|提示|输入)"
    r"[^.]{0,40}?"
    r"(?:exceed(?:s|ed)?|longer than|too long|超过|超出|超长)"
    r"|"
    r"(?:exceed(?:s|ed)?|longer than|too long|超过|超出|超长)"
    r"[^.]{0,40}?"
    r"(?:context|window|sequence|prompt|input|token|length|model|"
    r"模型|上下文|序列|提示|输入)"
)


def _is_context_overflow(detail: str) -> bool:
    """根据上游 400 响应体判断是否「上下文 / 输入超长」。

    两层判定：
      1) 精确子串命中（高精准，覆盖已知常见措辞）；
      2) 结构化正则兜底（高召回，长度名词 + 溢出动词共现即命中），
         跨 vLLM / SGLang / LMDeploy / MindIE / Ollama 通用，未来新框架也无需改代码。
    只匹配与长度相关的信号，避免把「模型不存在 / 参数非法 / 速率限制」等
    其它 400 误判为可裁剪。
    """
    if not detail:
        return False
    d = detail.lower()
    if any(h in d for h in _CTX_OVERFLOW_HINTS):
        return True
    return bool(_CTX_OVERFLOW_RE.search(d))


def _truncate_messages(messages: list, level: int) -> list:
    """上下文超长时裁剪消息：保留 system 提示词，丢弃最早的若干轮对话，
    并对剩余超长单条消息做截断。level 越大裁剪越激进（1,2,3...）。

    关键：按「完整轮次」丢弃 —— assistant 的 tool_calls 必须与其后的 tool 结果
    成对删除，否则会破坏 OpenAI 工具调用配对，导致服务端二次报错（依旧 400）。
    """
    level = max(1, int(level))
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    body = [m for m in messages if m.get("role") != "system"]

    # 1) 按完整轮次分组（assistant + tool_calls 与其后的 tool 结果视为一个单元）
    units: list = []
    i = 0
    while i < len(body):
        m = body[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            j = i + 1
            while j < len(body) and body[j].get("role") == "tool":
                j += 1
            units.append(body[i:j])
            i = j
        else:
            units.append([m])
            i += 1

    # 2) 丢弃最早的若干单元（至少保留最后一个，确保仍有用户输入）
    drop = int(len(units) * 0.35 * level)
    drop = min(drop, max(0, len(units) - 1))
    units = units[drop:]

    # 3) 对剩余超长单条消息截断（即便删轮次仍可能单条过长）
    cap = max(1500, 24000 // level)
    # 系统提示词自身也可能过大（技能/长期记忆/知识库注入过多）。若不裁系统提示，
    # 仅靠删对话历史无法把请求压进窗口 → 陷入「你好也 400」的死局。故同样截断：
    # 保留头部（身份/早期技能），尾部丢弃。sys_cap 比单条 cap 宽松，尽量多保留技能。
    sys_cap = max(1500, 60000 // level)
    sys_out: list = []
    for m in sys_msgs:
        c = m.get("content")
        if isinstance(c, str) and len(c) > sys_cap:
            nm = dict(m)
            nm["content"] = c[:sys_cap] + \
                "\n...[系统提示过长已自动截断，建议减少已启用技能/长期记忆/知识库注入]"
            sys_out.append(nm)
        else:
            sys_out.append(m)
    flat: list = []
    for unit in units:
        for m in unit:
            c = m.get("content")
            if isinstance(c, str) and len(c) > cap:
                nm = dict(m)
                nm["content"] = c[:cap] + "\n...[上下文超长已自动截断]"
                flat.append(nm)
            else:
                flat.append(m)
    return sys_out + flat


#: system 归一化已下沉到 compat 层（多框架共用）。此别名保留旧调用点 / 回归用例。
_normalize_system_messages = compat.merge_system_messages


# ---------------------------------------------------------------------------
# 多推理框架自愈：单次请求内最多尝试的「修复动作」次数
#
#   vLLM / SGLang / LMDeploy / MindIE / Ollama 对 OpenAI 协议的实现差异较大
#   （详见 ./compat.py）。请求失败时先由 compat.diagnose 判定是否属于可自愈的
#   兼容问题（不支持 tools / 拒绝未知字段 / content 结构非法 / 角色需交替 ...），
#   是则就地放宽画像重试；成功后把结论写进端点能力缓存，后续请求不再重复试错。
# ---------------------------------------------------------------------------
_COMPAT_MAX_REPAIRS = 5



# ---------------------------------------------------------------------------
# 进程级共享连接池
#
#   历史缺陷：LLMClient 在 __init__ 里各自新建 httpx.AsyncClient，而 agent.run()
#   为实现「按用户隔离模型」会**每轮请求**重建一次 LLMClient（见 agent.py），
#   MoA / adapters 亦会按需构建。这些实例的 aclose() 零调用点 ⇒ 每次对话都泄漏
#   一个连接池（含 keep-alive socket 与 SSL 上下文），长跑必然 FD 耗尽。
#
#   修复：HTTP 传输参数对所有实例完全一致（LLMClient 实例之间只有 cfg / api_mode
#   不同，这两者不参与传输层），因此全进程共用一个 AsyncClient 既安全又省资源，
#   还能真正复用 keep-alive 连接。关停时由 lifespan 统一 aclose。
# ---------------------------------------------------------------------------
# 连接超时设短：本地推理端点（Ollama / vLLM）未启动时，防火墙常静默丢弃 SYN，
# 默认 120s 总超时会让「无可用 LLM」的失败反馈卡很久；5s 连接超时即可快速判定。
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=10.0)
_HTTP_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20,
                            keepalive_expiry=30.0)
_shared_http: Optional[httpx.AsyncClient] = None
_shared_http_lock = threading.Lock()


def get_shared_http() -> httpx.AsyncClient:
    """获取（惰性创建）进程级共享 httpx.AsyncClient。

    双重检查加锁：FastAPI 单事件循环下不会并发进入，但 cron / 线程池里的同步
    调用方也可能触发首次创建，故用线程锁兜底。若此前被 aclose 过（如测试用例
    反复起停事件循环），`is_closed` 为真时自动重建，避免复用已关闭的池。
    """
    global _shared_http
    cli = _shared_http
    if cli is None or cli.is_closed:
        with _shared_http_lock:
            cli = _shared_http
            if cli is None or cli.is_closed:
                cli = httpx.AsyncClient(timeout=_HTTP_TIMEOUT, limits=_HTTP_LIMITS)
                _shared_http = cli
    return cli


async def aclose_shared_http() -> None:
    """关闭共享连接池（应用关停时调用；再次使用会自动惰性重建）。"""
    global _shared_http
    with _shared_http_lock:
        cli, _shared_http = _shared_http, None
    if cli is not None and not cli.is_closed:
        try:
            await cli.aclose()
        except Exception:  # noqa: BLE001 —— 关停期异常不应影响退出流程
            pass


class LLMClient:
    """统一 LLM 客户端：封装 chat / stream / embed，并内置回退链。"""

    def __init__(self, cfg: ZhishuConfig, api_mode: str = "openai"):
        self.cfg = cfg
        self.api_mode = api_mode
        # 回退链切换记录：本次 chat/stream 调用过程中，因某 Provider 故障而自动切到
        # 备用 Provider 时追加中文提示，供 agent 层以 warning 事件透传给用户（对标
        # Hermes 的 _pending_fallback_notice 一次性提示）。每次调用开始都会被清空。
        self._fallback_log: list[str] = []

    def consume_fallback_messages(self) -> list[str]:
        """取出并清空本次调用累计的回退提示（agent 层在拿到回复后调用一次）。"""
        msgs = self._fallback_log
        self._fallback_log = []
        return msgs

    @property
    def _http(self) -> httpx.AsyncClient:
        """所有实例共享同一连接池 —— 构造 LLMClient 不再产生任何资源。"""
        return get_shared_http()

    async def aclose(self):
        """关闭共享连接池。

        注意语义：连接池是进程级共享的，这里关闭的是**全局**池，仅应由应用
        关停钩子（main.lifespan）调用；业务代码里构造的临时 LLMClient 无需、
        也不应调用本方法（构造本身不占资源）。
        """
        await aclose_shared_http()

    # --------------------------- 非流式对话 ---------------------------
    async def chat(
        self,
        messages: list[Message],
        model: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        prefer: Optional[str] = None,
        tool_choice: Any = "auto",
    ) -> dict:
        if self.api_mode == "moa":
            from ..agent.moa import MoAClient

            return await MoAClient(self.cfg).chat(
                messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            )
        self._fallback_log = []
        chain = self._build_chain(model)
        last_err: Optional[Exception] = None
        last_hint: str = ""
        tried: list[str] = []
        prev_name: Optional[str] = None
        # 冷却期内：整条链上次全败，本次不再做退避重试，直接快失败，避免 agent 每个 step
        # 都重跑完整的重试链（那样会非常卡）。
        global _CHAIN_EXHAUSTED_AT
        in_cooldown = (time.monotonic() - _CHAIN_EXHAUSTED_AT) < _FALLBACK_EXHAUSTED_COOLDOWN_S
        for idx, (pc, mdl) in enumerate(chain):
            tried.append(pc.name)
            # 切到非首个 Provider 时，记录「已自动切换备用」提示（Eager-fallback 发生后）
            if idx > 0 and prev_name:
                self._fallback_log.append(
                    f"主用 Provider「{prev_name}」不可用（{last_hint or '故障'}），"
                    f"已自动切换到备用「{pc.name}」继续。")
            max_retries = 0 if in_cooldown else _LLM_MAX_RETRIES
            for attempt in range(max_retries + 1):
                try:
                    return await self._chat_once(pc, mdl, messages, tools,
                                                 temperature, max_tokens, tool_choice)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    info = classify_llm_error(e)
                    last_hint = info.user_hint
                    if attempt >= max_retries:
                        break
                    # 非网络抖动类故障（限流/鉴权/模型不可用/空响应/5xx）：本 Provider 不
                    # 再重试，立刻切下一个备用 —— 这正是消除「卡死/自动中断」体感的关键。
                    if not info.transient:
                        break
                    # 限流/空响应等：最多原地重试 1 次确认非偶发，随后也 eager 切备用。
                    if info.eager_fallback and attempt >= 1:
                        break
                    delay = _retry_after_seconds(e)
                    if delay is None:
                        # 指数退避 + 抖动，避免多个子智能体同步重试再次撞满限流窗口
                        delay = min(_LLM_RETRY_BASE * (2 ** attempt), _LLM_RETRY_MAX)
                        delay *= (0.7 + random.random() * 0.6)
                    await asyncio.sleep(delay)
            prev_name = pc.name
        # 整条回退链都失败：记录冷却时间戳，并给出比「所有 Provider 均不可用」更友好的提示。
        _CHAIN_EXHAUSTED_AT = time.monotonic()
        if last_err is not None and classify_llm_error(last_err).reason == RATE_LIMIT:
            hint = ("多智能体编排会在短时间内密集调用模型导致持续限流；请降低并发/减少子智能体"
                    "数量，或在「模型管理」中再配置一个 Provider 作为回退。")
        else:
            hint = ("请检查 LLM Provider 配置（API Key / base_url）或本地推理服务"
                    "（Ollama / vLLM）是否可用。")
        raise AllProvidersFailedError(tried, last_err or RuntimeError("未知错误"), hint=hint)

    @staticmethod
    def _is_local(base_url: str) -> bool:
        """判断是否为本地/内网推理端点（Ollama / vLLM 等，无需 API Key）。"""
        u = (base_url or "").lower()
        return any(seg in u for seg in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))

    def _build_chain(self, prefer: Optional[str]):
        ordered = self.cfg.ordered_providers()
        # 候选链：先放显式指定的 prefer / default_model，再放其余有序 Provider。
        # 关键修复：prefer / default_model 也走与「有序列表」相同的「无 Key 且非本地则跳过」逻辑，
        # 否则缺 Key 的默认模型会被强制注入链首，发出无 Authorization 头的请求，
        # 被网关以 401 拒绝，从而把「缺 Key」这一根因掩盖成「所有 Provider 均不可用」。
        candidates: list[tuple] = []
        added: set[str] = set()  # 已入链的 Provider 名，避免重复追加（尤其 default_model 路径）
        if prefer:
            pc, mdl = self.cfg.resolve_model(prefer)
            candidates.append((pc, mdl))
            added.add(pc.name)
        if model_override := (self.cfg.default_model if not prefer else None):
            pc, mdl = self.cfg.resolve_model(model_override)
            if pc.name not in added:
                candidates.append((pc, mdl))
                added.add(pc.name)
        for pc in ordered:
            if pc.name in added:
                continue
            candidates.append((pc, pc.models[0] if pc.models else "local-model"))
            added.add(pc.name)

        chain = []
        missing_keys: list[tuple] = []  # (provider name, base_url) 缺 Key 的云端 Provider
        for pc, mdl in candidates:
            # 跳过「无 API Key 且非本地」的 Provider：云端密钥缺失必败，
            # 逐个发起网络探测既无效又拖慢失败反馈（被代理拦截时尤为明显）。
            # 本地端点（Ollama / vLLM）即使无 Key 也应尝试（连不上会快速拒绝）。
            if not pc.api_key and not self._is_local(pc.base_url):
                if pc.name not in [n for n, _ in missing_keys]:
                    missing_keys.append((pc.name, pc.base_url))
                continue
            chain.append((pc, mdl))
        if not chain:
            if missing_keys:
                names = "、".join(f"{n}（{u}）" for n, u in missing_keys)
                raise RuntimeError(
                    f"所有已配置的 LLM Provider 均缺少 API Key 或不可达：{names}。"
                    "请在「模型管理」中为这些 Provider 填写有效的 API Key"
                    "（本地推理端点 Ollama/vLLM 可留空 Key 但需开启并可达）。"
                )
            # 完全跟随配置：没有任何已配置可用的模型时给出明确指引，而非静默尝试预设端点。
            raise RuntimeError(
                "未配置任何可用的 LLM 模型。请在「模型管理」中添加 Provider"
                "（填写 API Key，或启用本地端点如 Ollama/vLLM）并设置默认模型。"
            )
        return chain

    def _prepare(self, transport, pc, model, messages, tools, temperature,
                 max_tokens, tool_choice, profile, stream: bool):
        """按兼容画像组装一次请求体（消息规整 + 参数裁剪）。

        `endpoint_tools_ok` 与 `send_tools` 必须分开：
          * 端点**支持**工具但本轮不传 tools（如 MoA 的收尾调用）时，历史中的
            tool_calls / role=tool 仍然合法，不能摊平；
          * 端点**不支持**工具时，历史里的工具轮次也必须摊平成文本，否则即便
            不传 tools 参数，未知 role 依旧会被 400 拒绝。
        """
        endpoint_tools_ok = profile.supports_tools is not False
        send_tools = bool(tools) and endpoint_tools_ok
        msgs = compat.sanitize_messages(messages, profile,
                                        tools_enabled=endpoint_tools_ok)
        kw = transport.build_kwargs(
            msgs, tools if send_tools else None,
            temperature=temperature, max_tokens=max_tokens,
            stream=stream, model=model, tool_choice=tool_choice,
        )
        kw = compat.sanitize_kwargs(kw, profile, tools_enabled=send_tools)
        # Prompt 缓存（对标 Hermes prompt_caching.py）：在稳定前缀（system 末块 + 末 tool）
        # 挂 cache_control / 置 prompt_cache，使 Provider 的 KV 前缀缓存命中，缩短多步推理耗时。
        # 注入发生在 sanitize 之后，不会被兼容层剥除；off 模式原样返回（零回归）。
        try:
            kw = apply_prompt_cache(kw, pc, self.cfg.agent.prompt_cache)
        except Exception:  # noqa: BLE001 —— 缓存注入失败绝不应影响主链路
            pass
        return kw

    async def _chat_once(self, pc, model, messages, tools, temperature, max_tokens,
                         tool_choice: Any = "auto") -> dict:
        transport = get_transport(self.api_mode)
        profile = compat.effective_profile(pc, model)
        base_msgs = list(messages)
        mt = max_tokens
        ctx_level = 0
        applied: list[str] = []
        last_detail = ""
        url = pc.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()

        # 单次调用的总尝试次数 = 上下文裁剪次数 + 兼容修复次数 + 首次
        for _ in range(_CTX_TRUNCATE_RETRIES + _COMPAT_MAX_REPAIRS + 1):
            kw = self._prepare(transport, pc, model, base_msgs, tools, temperature,
                               mt, tool_choice, profile, stream=False)
            try:
                resp = await self._http.post(url, json=kw, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                sc = e.response.status_code if e.response is not None else 0
                detail = _extract_upstream_detail(e.response) if e.response is not None else ""
                last_detail = detail or last_detail
                # 1) 上下文超长：裁剪历史 + 缩短 max_tokens 后重试
                if sc == 400 and _is_context_overflow(detail):
                    if ctx_level < _CTX_TRUNCATE_RETRIES:
                        ctx_level += 1
                        base_msgs = _truncate_messages(base_msgs, ctx_level)
                        mt = max(512, int(mt * 0.6))
                        continue
                    # 已尽力裁剪（历史 + 超长单条 + 超长系统提示）仍超长 → 明确报错，
                    # 避免被聚合层伪装成「所有 Provider 均不可用」误导用户。
                    raise RuntimeError(
                        f"上下文超长：已自动裁剪对话历史/超长消息/系统提示，请求仍超出模型"
                        f"上下文窗口（{detail or '上游返回 HTTP 400'}）。这通常意味着系统提示词"
                        f"（已启用技能 / 长期记忆 / 知识库注入）本身过大。请：①在「模型管理」"
                        f"减少该 Provider 的已启用技能或调大 context_length；②或开启 "
                        f"agent.skills_progressive（仅列技能清单、按需读取）。"
                    ) from e
                # 2) 推理框架兼容问题：放宽画像后重试（每种修复动作只尝试一次，不会死循环）
                repair = compat.diagnose(sc, detail)
                if repair and repair not in applied and len(applied) < _COMPAT_MAX_REPAIRS:
                    applied.append(repair)
                    if repair == compat.REPAIR_SHRINK_TOKENS:
                        mt = max(256, min(mt, 1024))
                    else:
                        profile = compat.apply_repair(repair, profile)
                    continue
                raise RuntimeError(
                    f"Provider「{pc.name}」返回 HTTP {sc}"
                    + (f"：{detail}" if detail else f"：{e}")
                    + self._compat_hint(pc, profile, detail)
                ) from e
            out = transport.normalize_response(resp.json())
            # 若上游网关 / 代理返回「200 + 错误 JSON」（而非 4xx/5xx），
            # normalize_response 后依旧不含 choices —— 视为调用失败并抛出，
            # 以触发回退链，最终在没有可用 Provider 时统一抛 RuntimeError。
            # 否则错误体会被当成功结果原样返回，导致上层 resp["choices"] 直接 KeyError。
            if not isinstance(out, dict) or "choices" not in out:
                raise RuntimeError(f"Provider「{pc.name}」未返回有效补全：{str(out)[:200]}")
            for r in applied:   # 修复奏效 → 记住该端点能力，后续请求免去试错成本
                compat.remember(pc, model, r)
            return out
        raise RuntimeError(
            f"Provider「{pc.name}」重试 {_CTX_TRUNCATE_RETRIES + _COMPAT_MAX_REPAIRS} "
            f"次后仍失败（已尝试裁剪上下文与兼容降级）：{last_detail}"
        )

    @staticmethod
    def _compat_hint(pc, profile, detail: str) -> str:
        """错误信息尾部追加「兼容画像」提示，方便用户定位是不是框架差异导致的。"""
        if not detail:
            return ""
        if compat.diagnose(400, detail) is None:
            return ""
        return (f"（当前按「{profile.describe()}」兼容画像调用，若后端为 SGLang / "
                f"LMDeploy / MindIE 等，请在「模型管理」中把 Provider「{pc.name}」"
                f"的推理框架显式选对）")

    # --------------------------- 流式对话 ---------------------------
    async def stream(self, messages, model=None, tools=None,
                     temperature=0.7, max_tokens=2048, prefer=None) -> AsyncIterator[str]:
        if self.api_mode == "moa":
            from ..agent.moa import MoAClient

            async for piece in MoAClient(self.cfg).stream(
                messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            ):
                yield piece
            return
        self._fallback_log = []
        chain = self._build_chain(model)
        last_err = None
        last_hint = ""
        tried: list[str] = []
        prev_name: Optional[str] = None
        for idx, (pc, mdl) in enumerate(chain):
            tried.append(pc.name)
            if idx > 0 and prev_name:
                self._fallback_log.append(
                    f"主用 Provider「{prev_name}」不可用（{last_hint or '故障'}），"
                    f"已自动切换到备用「{pc.name}」继续。")
            try:
                async for piece in self._stream_once(pc, mdl, messages, tools, temperature, max_tokens):
                    yield piece
                return
            except Exception as e:
                last_err = e
                last_hint = classify_llm_error(e).user_hint
                # 流式下不原地重试（避免半截流），直接切下一个备用 Provider（eager 切换）
                continue
            prev_name = pc.name
        hint = "请检查 LLM Provider 配置（API Key / base_url）或本地推理服务（Ollama / vLLM）是否可用。"
        raise AllProvidersFailedError(tried, last_err or RuntimeError("未知错误"), hint=hint)

    async def _stream_once(self, pc, model, messages, tools, temperature, max_tokens):
        transport = get_transport(self.api_mode)
        profile = compat.effective_profile(pc, model)
        base_msgs = list(messages)
        mt = max_tokens
        ctx_level = 0
        applied: list[str] = []
        last_detail = ""
        url = pc.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        for _ in range(_CTX_TRUNCATE_RETRIES + _COMPAT_MAX_REPAIRS + 1):
            kw = self._prepare(transport, pc, model, base_msgs, tools, temperature,
                               mt, "auto", profile, stream=True)
            try:
                async with self._http.stream("POST", url, json=kw, headers=headers) as r:
                    try:
                        r.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        sc = r.status_code
                        detail = ""
                        try:
                            await r.aread()
                            detail = _extract_upstream_detail(r)
                        except Exception:
                            pass
                        last_detail = detail or last_detail
                        # 上下文超长：自动裁剪历史 + 缩短 max_tokens 后重试（最多 _CTX_TRUNCATE_RETRIES 次）。
                        # continue 会先退出 async with（关闭流），再进入下一轮裁剪重试；重试发生在首个 yield 之前，
                        # 因此流式输出不会因中途重试而出现错位 / 重复。
                        if sc == 400 and ctx_level < _CTX_TRUNCATE_RETRIES and _is_context_overflow(detail):
                            ctx_level += 1
                            base_msgs = _truncate_messages(base_msgs, ctx_level)
                            mt = max(512, int(mt * 0.6))
                            continue
                        # 推理框架兼容问题：放宽画像后重试（同样发生在首个 yield 之前）
                        repair = compat.diagnose(sc, detail)
                        if repair and repair not in applied and len(applied) < _COMPAT_MAX_REPAIRS:
                            applied.append(repair)
                            if repair == compat.REPAIR_SHRINK_TOKENS:
                                mt = max(256, min(mt, 1024))
                            else:
                                profile = compat.apply_repair(repair, profile)
                            continue
                        raise RuntimeError(
                            f"Provider「{pc.name}」返回 HTTP {sc}"
                            + (f"：{detail}" if detail else f"：{e}")
                            + self._compat_hint(pc, profile, detail)
                        ) from e
                    for _r in applied:
                        compat.remember(pc, model, _r)
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                        if "tool_calls" in delta:
                            yield f"\u0000TOOLCALL\u0000{json.dumps(delta['tool_calls'])}\u0000"
                return
            except RuntimeError:
                raise
        raise RuntimeError(
            f"Provider「{pc.name}」重试 {_CTX_TRUNCATE_RETRIES + _COMPAT_MAX_REPAIRS} "
            f"次后仍失败（已尝试裁剪上下文与兼容降级）：{last_detail}"
        )

    # --------------------------- 上游重试 ---------------------------
    async def _request_with_retry(self, method, url, *, headers=None, json=None,
                                  follow_redirects=False) -> httpx.Response:
        media = self.cfg.media
        attempt = 0
        last_resp = None
        last_err = None
        while True:
            try:
                resp = await self._http.request(
                    method, url, headers=headers or {}, json=json,
                    follow_redirects=follow_redirects,
                )
            except httpx.TransportError as e:
                last_resp = None
                last_err = e
            else:
                last_resp = resp
                last_err = None
                if resp.status_code in media.retry_codes:
                    last_err = _RetryableStatus(resp)
                else:
                    return resp
            if attempt >= media.max_retries:
                break
            delay = min(media.retry_base_delay * (2 ** attempt), media.retry_max_delay)
            delay *= (0.5 + random.random())
            await asyncio.sleep(delay)
            attempt += 1
        if last_resp is not None:
            detail = _extract_upstream_detail(last_resp)
            raise RuntimeError(
                f"上游服务繁忙（HTTP {last_resp.status_code}），已重试 {media.max_retries} 次仍失败。"
                + (f" 原因：{detail}" if detail else "")
            )
        raise RuntimeError(
            f"调用上游服务失败（网络错误），已重试 {media.max_retries} 次：{last_err}"
        )

    # --------------------------- 图像生成 ---------------------------
    async def generate_image(self, pc, model, prompt, size=None, image=None) -> dict:
        media = self.cfg.media
        url = pc.base_url.rstrip("/") + media.image_path
        payload: dict = {"model": model, "prompt": prompt, "size": size or media.image_size}
        if image:
            imgs = image if isinstance(image, list) else [image]
            payload["image"] = [i for i in imgs if i]
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        resp = await self._request_with_retry("POST", url, headers=headers, json=payload)
        data = resp.json()
        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"图像接口未返回结果：{str(data)[:200]}")
        first = items[0]
        if first.get("url"):
            return {"url": first["url"]}
        if first.get("b64_json"):
            return {"b64": first["b64_json"]}
        raise RuntimeError("图像接口返回中既无 url 也无 b64_json")

    # --------------------------- 视频生成 ---------------------------
    async def create_video_task(self, pc, model, prompt, size=None, image=None) -> str:
        media = self.cfg.media
        url = pc.base_url.rstrip("/") + media.video_path
        w, h = self._parse_size(size or media.video_size)
        payload = {
            "model": model, "prompt": prompt, "width": w, "height": h,
            "num_frames": media.video_num_frames, "frame_rate": media.video_frame_rate,
        }
        if image:
            payload["image"] = image
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        resp = await self._request_with_retry("POST", url, headers=headers, json=payload)
        data = resp.json()
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise RuntimeError(f"视频任务未返回 task_id：{str(data)[:200]}")
        return str(task_id)

    async def poll_video_once(self, pc, task_id) -> dict:
        media = self.cfg.media
        path = media.video_poll_path.format(task_id=task_id)
        url = pc.base_url.rstrip("/") + path
        headers = {}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        resp = await self._request_with_retry("GET", url, headers=headers)
        data = resp.json()
        status = (data.get("status") or "").lower()
        # 优先取顶层 video_url / url；仅当二者皆空且 data 为列表时，再回退到列表首项。
        # 注意：此前整段表达式被三元运算符 `if isinstance(data.get("data"), list)` 包裹，
        # 导致「无 data 键但顶层已有 video_url」时被错误地置为 None。此处显式分离。
        video_url = data.get("video_url") or data.get("url")
        if not video_url:
            data_list = data.get("data")
            if isinstance(data_list, list) and data_list:
                first = data_list[0] if isinstance(data_list[0], dict) else {}
                video_url = first.get("url")
        return {"status": status, "progress": data.get("progress"),
                "video_url": video_url, "error": data.get("error"), "raw": data}

    # --------------------------- 下载产物 ---------------------------
    async def download(self, url: str) -> bytes:
        resp = await self._request_with_retry("GET", url, follow_redirects=True)
        return resp.content

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        try:
            w, h = size.lower().split("x", 1)
            return int(w), int(h)
        except Exception:
            return 1152, 768

    # --------------------------- Embedding ---------------------------
    async def embed(self, texts, pc: Optional[ProviderConfig] = None) -> list[list[float]]:
        pc = pc or self.cfg.get_provider("qwen") or next(iter(self.cfg.providers.values()), None)
        if not pc:
            raise RuntimeError("无可用 embedding provider")
        url = pc.base_url.rstrip("/") + "/embeddings"
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        out = []
        for t in texts:
            resp = await self._http.post(url, json={"model": pc.models[0], "input": t}, headers=headers)
            resp.raise_for_status()
            out.append(resp.json()["data"][0]["embedding"])
        return out
