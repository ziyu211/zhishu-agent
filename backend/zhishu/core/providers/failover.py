"""智枢智能体 —— LLM 故障分类与主动回退决策。

对标 Hermes ``agent/error_classifier.py`` 的 ``classify_api_error``，但面向国产
网关模型（通义/智谱/DeepSeek/Ollama/vLLM）做了中文语义增强：

  * 不再只有「瞬时 / 永久」二分，而是把故障细分为
    限流 / 鉴权 / 模型不可用 / 内容被拦截 / 空响应 / 服务端错误 / 网络抖动 / 未知；
  * 每类给出三个决策标志：
      - ``transient``   —— 是否值得在同一 Provider 上退避重试（网络抖动值得，限流不值得）；
      - ``eager_fallback`` —— 是否应**立刻**切到回退链下一个 Provider，而不在故障 Provider
                            上把重试额度耗尽（限流/鉴权/空响应/5xx 都该立刻切，避免「卡死」）；
      - ``user_hint``   —— 给用户看的中文故障原因，用于「已自动切换备用模型」提示。

设计目标：当主用 Provider 限流/宕机时，智枢应**静默、快速地**切到下一个可用 Provider
继续，而不是在同一 Provider 上做 5 次指数退避重试（那正是用户感知「卡死 / 自动中断」的
重要来源）。这比 Hermes 更进一步：Hermes 仅在部分错误类型上 eager-fallback，智枢对所有
「非网络抖动」的故障都 eager-fallback。
"""
from __future__ import annotations

import httpx


# 故障原因枚举（用于日志/可观测性，不影响控制流）
RATE_LIMIT = "rate_limit"          # 限流 / 配额耗尽
AUTH = "auth"                      # 鉴权失败（密钥无效/过期）
MODEL_INVALID = "model_invalid"    # 模型不存在 / 该端点不支持
CONTENT_FILTER = "content_filter"  # 内容被安全策略拦截
EMPTY_RESPONSE = "empty_response"  # 网关返回 200 但无有效补全（空响应/畸形）
SERVER_ERROR = "server_error"      # 5xx / 网关 502/503/504
TRANSIENT_NET = "transient_net"    # 连接超时 / 读超时 / TLS —— 网络抖动
UNKNOWN = "unknown"


# 终端错误响应体里常见的「鉴权失败」关键字（中英文网关都覆盖）
_AUTH_HINTS = (
    "unauthorized", "invalid api key", "invalid authentication",
    "authentication failed", "api key", "apikey", "token",
    "鉴权", "密钥", "未授权", "认证失败", "401", "403",
)
# 限流关键字
_RATELIMIT_HINTS = (
    "rate limit", "too many requests", "quota", "quota exceeded",
    "请求过于频繁", "限流", "频率", "rate_limit", "429",
)
# 内容拦截关键字
_FILTER_HINTS = (
    "content filter", "content_filter", "sensitive", "policy",
    "内容被拦截", "安全策略", "敏感", "审核", "refuse", "declined",
)
# 模型不存在关键字
_MODEL_HINTS = (
    "model not found", "does not exist", "unknown model",
    "模型不存在", "模型不可用", "invalid model", "no such model",
)
# 空响应关键字（200 但无 choices）
_EMPTY_HINTS = (
    "未返回有效补全", "no choices", "empty", "空响应", "invalid completion",
)


def _status_of(exc: Exception) -> "tuple[int, str]":
    """尽量从异常里抠出 (状态码, 响应体文本)。"""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    if code is None:
        # RuntimeError 由 _chat_once 抛出时，状态码可能挂在 args 文本里
        txt = " ".join(str(a) for a in getattr(exc, "args", ()) if isinstance(a, str))
        for token in ("429", "401", "403", "400", "500", "502", "503", "504"):
            if f"HTTP {token}" in txt or f"返回 HTTP {token}" in txt:
                return int(token), txt
        return 0, txt
    try:
        body = resp.text or ""
    except Exception:
        body = ""
    return int(code), body


def classify_llm_error(exc: Exception) -> "FailoverInfo":
    """把一次 LLM 调用异常分类为结构化故障信息。

    返回 :class:`FailoverInfo`，调用方据其决定是否在同一 Provider 重试、是否立刻切备用。
    """
    code, text = _status_of(exc)
    low = (text or "").lower()

    # 1) 鉴权失败：密钥必败，重试同一 Provider 毫无意义 → 立刻切备用
    if code in (401, 403) or any(h in low for h in _AUTH_HINTS):
        return FailoverInfo(AUTH, transient=False, eager_fallback=True,
                            user_hint="鉴权失败（API Key 无效/过期）")

    # 2) 限流 / 配额耗尽：继续打同一 Provider 只会持续 429 → 立刻切备用
    if code == 429 or any(h in low for h in _RATELIMIT_HINTS):
        return FailoverInfo(RATE_LIMIT, transient=False, eager_fallback=True,
                            user_hint="限流/配额耗尽")

    # 3) 模型不可用：该端点根本没这模型 → 立刻切备用
    if code == 400 and any(h in low for h in _MODEL_HINTS):
        return FailoverInfo(MODEL_INVALID, transient=False, eager_fallback=True,
                            user_hint="模型在该 Provider 不可用")

    # 4) 内容被拦截：安全策略拒绝，换备用模型可能放通 → 立刻切
    if any(h in low for h in _FILTER_HINTS):
        return FailoverInfo(CONTENT_FILTER, transient=False, eager_fallback=True,
                            user_hint="内容被安全策略拦截")

    # 5) 空响应 / 畸形（网关 200 但无 choices）：同一 Provider 再试也多半同样 → 立刻切
    if any(h in low for h in _EMPTY_HINTS) or "未返回有效补全" in text:
        return FailoverInfo(EMPTY_RESPONSE, transient=False, eager_fallback=True,
                            user_hint="模型返回空响应")

    # 6) 服务端错误（5xx / 网关 502/503/504）：该 Provider 大概率不健康 → 立刻切备用
    if code in (500, 502, 503, 504, 529):
        return FailoverInfo(SERVER_ERROR, transient=True, eager_fallback=True,
                            user_hint="服务端错误/网关不可用")

    # 7) 纯网络抖动（超时/连接失败/TLS）：值得在同一 Provider 退避重试 1~2 次，
    #    但不 eager 切（否则所有 Provider 共享一条抖动网络时会反复横跳）。
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                        httpx.RemoteProtocolError, httpx.PoolTimeout)):
        return FailoverInfo(TRANSIENT_NET, transient=True, eager_fallback=False,
                            user_hint="网络连接超时/抖动")

    # 8) 其它 400：可能是请求参数非法（兼容层会自愈），先在本 Provider 重试一次
    if code == 400:
        return FailoverInfo(UNKNOWN, transient=True, eager_fallback=False,
                            user_hint="请求被拒绝（可能参数不兼容）")

    # 9) 兜底：未知错误，保守地在本 Provider 退避重试，实在不行再切
    return FailoverInfo(UNKNOWN, transient=True, eager_fallback=False, user_hint="未知错误")


class FailoverInfo:
    """一次 LLM 故障的结构化描述（对标 Hermes ``FailoverReason``）。"""

    __slots__ = ("reason", "transient", "eager_fallback", "user_hint")

    def __init__(self, reason: str, *, transient: bool, eager_fallback: bool,
                 user_hint: str):
        self.reason = reason
        self.transient = transient
        self.eager_fallback = eager_fallback
        self.user_hint = user_hint

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return (f"FailoverInfo(reason={self.reason!r}, transient={self.transient}, "
                f"eager={self.eager_fallback}, hint={self.user_hint!r})")


class AllProvidersFailedError(RuntimeError):
    """整条回退链都失败时的统一异常（``RuntimeError`` 子类，向后兼容现有 except）。

    携带 ``providers``（尝试过的 Provider 名列表）与 ``hint``（给用户看的中文建议），
    便于 agent 层给出比「所有 LLM Provider 均不可用」更友好的兜底提示。
    """

    def __init__(self, providers: list[str], cause: Exception, hint: str = ""):
        self.providers = list(providers)
        self.cause = cause
        self.hint = hint
        msg = (f"所有已配置的 LLM Provider（{', '.join(self.providers) or '无'}）"
               f"均不可用：{cause}")
        if hint:
            msg += f"。{hint}"
        super().__init__(msg)
