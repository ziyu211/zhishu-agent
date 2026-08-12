"""智枢智能体 —— 多推理框架兼容层（vLLM / SGLang / LMDeploy / MindIE / Ollama ...）。

背景
----
「OpenAI 兼容」只是一个**松散约定**：各家推理框架对同一份请求体的容忍度差异很大，
同一段对话在 vLLM 上能跑，换到 LMDeploy / MindIE 上就会 400/422/500。实测到的差异：

  1. **system 位置**：vLLM / SGLang 要求 system 必须在消息数组最开头且通常仅一条，
     中途追加 system（智枢 agent 会这么做）→ HTTP 500 `system message must be at the beginning`。
  2. **content: null**：OpenAI 规范允许 assistant(tool_calls) 的 content 为 null，
     但 LMDeploy / MindIE / Ollama 的 pydantic 模型要求 str → 400 `none is not an allowed value`。
  3. **function calling**：并非所有框架 / 模型都带 tool 解析器（vLLM 需 `--enable-auto-tool-choice`，
     LMDeploy / MindIE 视模型而定，Ollama 视模型而定）→ 400 `tools is not supported`。
     此时不仅要去掉 tools，**历史里的 tool_calls / role=tool 消息也必须摊平成文本**，
     否则换成不带 tools 的请求依旧会因为未知 role 被拒。
  4. **可选参数**：`stream_options` / `parallel_tool_calls` 被 vLLM/SGLang/OpenAI 接受，
     但 LMDeploy / MindIE / Ollama 会以 `Extra inputs are not permitted` 拒绝。
  5. **角色交替**：部分国产模型的 chat template（ChatGLM / Baichuan 系）要求 user/assistant
     严格交替 → `conversation roles must alternate`。
  6. **max_tokens 上限**：LMDeploy 的 `max_new_tokens` 有硬上限，超出直接 400。

设计
----
双保险：

  * **静态画像 CompatProfile** —— 按 Provider 的 `compat` 字段（或从 base_url/名字自动推断）
    预先规避已知差异，不用等报错。
  * **动态自愈 diagnose()/REPAIR_*** —— 把上游错误映射为一个「修复动作」，客户端就地
    重试；成功后把结论写进进程级能力缓存 `runtime_caps`，同一端点后续请求直接带上，
    不再重复付出一次 400 的代价。

新增框架只需在 `PROFILES` 里加一行，无需改动 LLMClient 主流程。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# 兼容画像
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CompatProfile:
    """某一类推理框架的请求体约束集合。"""

    key: str
    label: str
    # ---- 消息约束 ----
    system_at_beginning: bool = True     # system 合并为一条并置顶（几乎所有框架都能接受，默认开）
    multimodal_content: bool = True      # 是否接受 content 为多模态数组；否则拍平为纯文本
    require_alternating_roles: bool = False  # 是否要求 user/assistant 严格交替
    # ---- 能力 ----
    supports_tools: Optional[bool] = None    # True=确定支持 / False=确定不支持 / None=未知（先试后降级）
    supports_tool_choice: bool = True
    supports_stream_options: bool = False
    supports_parallel_tool_calls: bool = False
    # ---- 参数 ----
    drop_params: tuple[str, ...] = ()        # 无条件从请求体剔除的字段
    extra_params: dict = field(default_factory=dict)  # 无条件补充的字段
    max_tokens_cap: Optional[int] = None     # 该框架的输出上限（None=不限制）

    def describe(self) -> str:
        return f"{self.label}（{self.key}）"


def _p(key: str, label: str, **kw: Any) -> CompatProfile:
    return CompatProfile(key=key, label=label, **kw)


#: 内置兼容画像。key 即 Provider 配置里的 `compat` 值。
PROFILES: dict[str, CompatProfile] = {
    # 云端标准 OpenAI 协议（含通义/DeepSeek/智谱/Kimi 等国产网关，容忍度高）
    "openai": _p("openai", "OpenAI 标准 / 云端网关",
                 supports_tools=True, supports_stream_options=True,
                 supports_parallel_tool_calls=True),
    # vLLM：system 必须置顶；tools 需启动时开 --enable-auto-tool-choice，故置未知
    "vllm": _p("vllm", "vLLM",
               supports_tools=None, supports_stream_options=True,
               supports_parallel_tool_calls=True),
    # SGLang：与 vLLM 接近，tool 解析器同样需显式开启
    "sglang": _p("sglang", "SGLang",
                 supports_tools=None, supports_stream_options=True),
    # LMDeploy（api_server）：pydantic 严格，拒绝 content:null 与未知字段
    "lmdeploy": _p("lmdeploy", "LMDeploy",
                   supports_tools=None, supports_stream_options=False,
                   supports_parallel_tool_calls=False,
                   drop_params=("stream_options", "parallel_tool_calls", "logprobs",
                                "top_logprobs", "response_format")),
    # MindIE（昇腾 MindIE Service）：最保守，多模态数组与 tool_choice 常不支持
    "mindie": _p("mindie", "MindIE（昇腾）",
                 multimodal_content=False,
                 supports_tools=None, supports_tool_choice=False,
                 supports_stream_options=False, supports_parallel_tool_calls=False,
                 drop_params=("stream_options", "parallel_tool_calls", "logprobs",
                              "top_logprobs", "response_format", "seed", "n")),
    # Ollama /v1 兼容层：tools 依模型而定，未知字段一律拒绝
    "ollama": _p("ollama", "Ollama",
                 supports_tools=None, supports_stream_options=False,
                 drop_params=("stream_options", "parallel_tool_calls")),
    # Xinference：OpenAI 兼容度较高，但不认 stream_options
    "xinference": _p("xinference", "Xinference",
                     supports_tools=None, supports_stream_options=False,
                     drop_params=("stream_options",)),
    # HuggingFace TGI messages API
    "tgi": _p("tgi", "TGI（Text Generation Inference）",
              multimodal_content=False, supports_tools=None,
              supports_tool_choice=False, supports_stream_options=False,
              drop_params=("stream_options", "parallel_tool_calls")),
    # llama.cpp server
    "llamacpp": _p("llamacpp", "llama.cpp server",
                   supports_tools=None, supports_stream_options=False,
                   drop_params=("stream_options", "parallel_tool_calls")),
    # 未知端点：按最保守策略发请求，再靠动态自愈放宽
    "generic": _p("generic", "通用 / 自动探测",
                  multimodal_content=True, supports_tools=None,
                  supports_stream_options=False,
                  drop_params=("stream_options", "parallel_tool_calls")),
}

#: 常见别名 → 画像 key
ALIASES: dict[str, str] = {
    "": "",  # 空值交给自动推断
    "auto": "",
    "openai-compatible": "openai", "oai": "openai", "标准": "openai",
    "vllm-openai": "vllm", "vllm_openai": "vllm",
    "sgl": "sglang", "sglang-router": "sglang", "sglang_router": "sglang",
    "lmdeploy-api": "lmdeploy", "turbomind": "lmdeploy", "internlm": "lmdeploy",
    "mindie-service": "mindie", "mindieservice": "mindie", "ascend": "mindie",
    "npu": "mindie", "昇腾": "mindie", "atlas": "mindie",
    "ollama-openai": "ollama",
    "xinf": "xinference", "xorbits": "xinference",
    "text-generation-inference": "tgi", "huggingface": "tgi",
    "llama.cpp": "llamacpp", "llama_cpp": "llamacpp", "ggml": "llamacpp",
    "unknown": "generic", "custom": "generic",
}

#: 供前端下拉选择使用（顺序即展示顺序）
def profile_options() -> list[dict]:
    opts = [{"value": "", "label": "自动探测（推荐）"}]
    opts += [{"value": k, "label": PROFILES[k].label}
             for k in ("openai", "vllm", "sglang", "lmdeploy", "mindie",
                       "ollama", "xinference", "tgi", "llamacpp", "generic")]
    return opts


# ---- 自动推断 -------------------------------------------------------------
#: 端口 → 框架（各框架默认监听端口）
_PORT_HINTS = {
    11434: "ollama",
    23333: "lmdeploy",
    30000: "sglang",
    9997: "xinference",
    1025: "mindie", 1026: "mindie",
}
#: URL / 名称关键字 → 框架
_TEXT_HINTS = (
    ("ollama", "ollama"),
    ("lmdeploy", "lmdeploy"), ("turbomind", "lmdeploy"),
    ("sglang", "sglang"),
    ("mindie", "mindie"), ("ascend", "mindie"), ("npu", "mindie"),
    ("xinference", "xinference"),
    ("vllm", "vllm"),
    ("llama.cpp", "llamacpp"), ("llamacpp", "llamacpp"),
    ("tgi", "tgi"), ("text-generation", "tgi"),
)
#: 已知云端网关域名（走标准 openai 画像）
_CLOUD_HINTS = (
    "api.openai.com", "dashscope", "deepseek.com", "bigmodel.cn", "moonshot.cn",
    "baidubce.com", "minimax", "siliconflow", "ark.cn-", "volces.com",
    "aliyuncs.com", "azure.com", "sensecore", "01.ai", "stepfun",
)


def detect_compat(base_url: str = "", name: str = "") -> str:
    """从 base_url / Provider 名自动推断推理框架，推断不出时返回 'generic'。"""
    text = f"{base_url or ''} {name or ''}".lower()
    for kw, key in _TEXT_HINTS:
        if kw in text:
            return key
    if any(h in text for h in _CLOUD_HINTS):
        return "openai"
    try:
        port = urlparse(base_url or "").port
    except ValueError:
        port = None
    if port and port in _PORT_HINTS:
        return _PORT_HINTS[port]
    return "generic"


def resolve_profile(compat: str = "", base_url: str = "", name: str = "") -> CompatProfile:
    """把用户填写的 compat 值（可为空 / 别名 / 大小写混写）解析为兼容画像。"""
    key = (compat or "").strip().lower().replace(" ", "")
    key = ALIASES.get(key, key)
    if not key:
        key = detect_compat(base_url, name)
    return PROFILES.get(key) or PROFILES["generic"]


def profile_for(pc: Any) -> CompatProfile:
    """从 ProviderConfig 取兼容画像（字段缺失时自动推断，兼容旧配置）。"""
    return resolve_profile(getattr(pc, "compat", "") or "",
                           getattr(pc, "base_url", "") or "",
                           getattr(pc, "name", "") or "")


# ---------------------------------------------------------------------------
# 运行期能力缓存：把「某端点不支持 X」的结论记住，避免每轮都吃一次 400
# ---------------------------------------------------------------------------
CAP_NO_TOOLS = "no_tools"
CAP_NO_OPTIONAL = "no_optional"
CAP_PLAIN_CONTENT = "plain_content"
CAP_ALTERNATE = "alternate"

_CAP_LIMIT = 512


class RuntimeCaps:
    """进程级端点能力缓存（线程安全，进程重启即清空）。"""

    def __init__(self) -> None:
        self._d: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(base_url: str, model: str = "") -> str:
        return f"{(base_url or '').rstrip('/')}|{model or ''}"

    def get(self, key: str) -> set[str]:
        return set(self._d.get(key) or ())

    def has(self, key: str, cap: str) -> bool:
        return cap in (self._d.get(key) or ())

    def add(self, key: str, cap: str) -> None:
        with self._lock:
            if len(self._d) >= _CAP_LIMIT and key not in self._d:
                self._d.clear()   # 简单泄压：超限直接清空，重新学习成本可接受
            self._d.setdefault(key, set()).add(cap)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def snapshot(self) -> dict[str, list[str]]:
        return {k: sorted(v) for k, v in self._d.items()}


runtime_caps = RuntimeCaps()


def effective_profile(pc: Any, model: str = "") -> CompatProfile:
    """静态画像 + 运行期学到的结论 = 本次实际生效的画像。"""
    prof = profile_for(pc)
    caps = runtime_caps.get(RuntimeCaps.key(getattr(pc, "base_url", ""), model))
    if not caps:
        return prof
    patch: dict[str, Any] = {}
    if CAP_NO_TOOLS in caps:
        patch["supports_tools"] = False
    if CAP_NO_OPTIONAL in caps:
        patch["supports_stream_options"] = False
        patch["supports_parallel_tool_calls"] = False
        patch["drop_params"] = tuple(set(prof.drop_params) | {
            "stream_options", "parallel_tool_calls", "logprobs", "top_logprobs",
            "response_format",
        })
    if CAP_PLAIN_CONTENT in caps:
        patch["multimodal_content"] = False
    if CAP_ALTERNATE in caps:
        patch["require_alternating_roles"] = True
    return replace(prof, **patch) if patch else prof


# ---------------------------------------------------------------------------
# 消息 / 请求体规整
# ---------------------------------------------------------------------------
def _flatten_content(content: Any) -> str:
    """把多模态数组 content 拍平为纯文本（图片等非文本部分以占位符表示）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for it in content:
            if isinstance(it, str):
                parts.append(it)
            elif isinstance(it, dict):
                t = it.get("type")
                if t == "text" or "text" in it:
                    parts.append(str(it.get("text") or ""))
                elif t in ("image_url", "image"):
                    parts.append("[图片]")
                elif t in ("input_audio", "audio"):
                    parts.append("[音频]")
        return "\n".join(p for p in parts if p)
    return str(content)


def merge_system_messages(messages: list) -> list:
    """把所有 system 内容合并为一条并置于数组首位（其余消息相对顺序不变）。

    智枢 agent 会在工具调用 / 多智能体循环里向对话**中途**追加 system 提示，
    vLLM / SGLang 等会以 `system message must be at the beginning` 拒绝。
    合并不会破坏 assistant(tool_calls) 与 tool 结果的配对。
    """
    if not messages:
        return messages
    sys_idx = [i for i, m in enumerate(messages) if m.get("role") == "system"]
    if not sys_idx:
        return messages
    if len(sys_idx) == 1 and sys_idx[0] == 0:
        return messages
    sys_parts: list = []
    rest: list = []
    for m in messages:
        if m.get("role") == "system":
            c = _flatten_content(m.get("content"))
            if c:
                sys_parts.append(c)
        else:
            rest.append(m)
    merged = "\n\n".join(sys_parts)
    if merged:
        rest.insert(0, {"role": "system", "content": merged})
    return rest


def flatten_tool_messages(messages: list) -> list:
    """端点不支持 function calling 时，把工具轮次摊平成普通文本对话。

    仅去掉 tools 参数是不够的：历史里残留的 `assistant.tool_calls` 与
    `role="tool"` 消息同样会被这些端点判为非法（unknown role / extra field）。
    """
    out: list = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            body = _flatten_content(m.get("content"))
            out.append({"role": "user", "content": f"[工具结果] {body}"})
            continue
        if role == "assistant" and m.get("tool_calls"):
            body = _flatten_content(m.get("content"))
            calls: list[str] = []
            for tc in m.get("tool_calls") or []:
                fn = (tc or {}).get("function") or {}
                args = fn.get("arguments")
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args, ensure_ascii=False)
                    except Exception:  # noqa: BLE001
                        args = str(args)
                calls.append(f"{fn.get('name', '')}({args})")
            txt = (body + "\n" if body else "") + "[调用工具] " + "; ".join(calls)
            out.append({"role": "assistant", "content": txt.strip()})
            continue
        nm = dict(m)
        nm.pop("tool_calls", None)
        nm.pop("tool_call_id", None)
        out.append(nm)
    return out


def enforce_alternating(messages: list) -> list:
    """合并连续同角色消息，并确保首条（system 之后）为 user。

    ChatGLM / Baichuan 等 chat template 要求 user/assistant 严格交替，
    否则报 `conversation roles must alternate user/assistant/...`。
    """
    head = [m for m in messages if m.get("role") == "system"]
    body = [m for m in messages if m.get("role") != "system"]
    # 丢弃开头的 assistant（无对应 user 提问）
    while body and body[0].get("role") != "user":
        body.pop(0)
    merged: list = []
    for m in body:
        if merged and merged[-1].get("role") == m.get("role"):
            prev = merged[-1]
            a = _flatten_content(prev.get("content"))
            b = _flatten_content(m.get("content"))
            prev["content"] = (a + "\n\n" + b).strip() if (a and b) else (a or b)
        else:
            merged.append(dict(m))
    return head + merged


def sanitize_messages(messages: list, profile: CompatProfile,
                      tools_enabled: bool = True) -> list:
    """按兼容画像规整消息数组（纯函数，不修改入参）。"""
    msgs = [dict(m) for m in (messages or [])]

    if not tools_enabled or profile.supports_tools is False:
        msgs = flatten_tool_messages(msgs)

    if profile.system_at_beginning:
        msgs = merge_system_messages(msgs)

    out: list = []
    for m in msgs:
        nm = dict(m)
        c = nm.get("content")
        # 1) content: null → ""（LMDeploy / MindIE / Ollama 的 pydantic 要求 str）
        if c is None:
            nm["content"] = ""
        # 2) 多模态数组 → 纯文本（不支持视觉输入的端点会直接 400）
        elif isinstance(c, list) and not profile.multimodal_content:
            nm["content"] = _flatten_content(c)
        elif not isinstance(c, (str, list)):
            nm["content"] = str(c)
        # 3) tool 结果必须是字符串
        if nm.get("role") == "tool" and not isinstance(nm.get("content"), str):
            nm["content"] = _flatten_content(nm.get("content"))
        # 4) tool_calls.function.arguments 必须是 JSON 字符串（部分框架不接受 dict）
        tcs = nm.get("tool_calls")
        if isinstance(tcs, list) and tcs:
            fixed: list = []
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                ntc = dict(tc)
                fn = dict(ntc.get("function") or {})
                args = fn.get("arguments")
                if args is None:
                    fn["arguments"] = "{}"
                elif not isinstance(args, str):
                    try:
                        fn["arguments"] = json.dumps(args, ensure_ascii=False)
                    except Exception:  # noqa: BLE001
                        fn["arguments"] = str(args)
                if fn:
                    ntc["function"] = fn
                fixed.append(ntc)
            nm["tool_calls"] = fixed
        out.append(nm)

    if profile.require_alternating_roles:
        out = enforce_alternating(out)
    return out


def sanitize_kwargs(kw: dict, profile: CompatProfile,
                    tools_enabled: bool = True) -> dict:
    """按兼容画像规整请求体：剔除不被支持的可选参数、补充框架特有参数。"""
    out = dict(kw or {})
    if not tools_enabled or profile.supports_tools is False:
        out.pop("tools", None)
        out.pop("tool_choice", None)
        out.pop("parallel_tool_calls", None)
    elif not profile.supports_tool_choice:
        out.pop("tool_choice", None)
    if not profile.supports_stream_options:
        out.pop("stream_options", None)
    if not profile.supports_parallel_tool_calls:
        # 端点不支持并行工具调用：剥除该参数，避免被网关以 400 拒绝。
        out.pop("parallel_tool_calls", None)
    elif tools_enabled:
        # 主动告知模型：可在单个响应里并行发出多个相互独立的工具调用。
        # agent 循环会并发执行（asyncio.gather + 信号量，见 agent.py），
        # 把原本 N 次串行 LLM 往返压缩为 1 次 —— 这是「智枢比 Hermes 慢」的核心修复点。
        # 若端点实际拒绝该参数，is_param_unsupported 会触发 REPAIR_STRIP_OPTIONAL
        # 自愈（首次 400 后扒掉并写入 runtime_caps），最多一次重试，不死循环。
        out["parallel_tool_calls"] = True
    for p in profile.drop_params:
        out.pop(p, None)
    if profile.max_tokens_cap and isinstance(out.get("max_tokens"), int):
        out["max_tokens"] = min(out["max_tokens"], profile.max_tokens_cap)
    for k, v in (profile.extra_params or {}).items():
        out.setdefault(k, v)
    return out


# ---------------------------------------------------------------------------
# 动态自愈：把上游错误映射为修复动作
# ---------------------------------------------------------------------------
REPAIR_DROP_TOOLS = "drop_tools"          # 端点不支持 function calling
REPAIR_STRIP_OPTIONAL = "strip_optional"  # 端点拒绝未知 / 可选参数
REPAIR_PLAIN_CONTENT = "plain_content"    # content 结构不被接受（数组 / 非字符串）
REPAIR_ALTERNATE = "alternate"            # 要求 user/assistant 严格交替
REPAIR_SYSTEM = "system_first"            # system 必须置顶
REPAIR_SHRINK_TOKENS = "shrink_tokens"    # max_tokens / max_new_tokens 超出上限

#: 修复动作 → 运行期能力标记（None 表示不必缓存）
REPAIR_TO_CAP = {
    REPAIR_DROP_TOOLS: CAP_NO_TOOLS,
    REPAIR_STRIP_OPTIONAL: CAP_NO_OPTIONAL,
    REPAIR_PLAIN_CONTENT: CAP_PLAIN_CONTENT,
    REPAIR_ALTERNATE: CAP_ALTERNATE,
}

_NEG = ("not support", "not supported", "unsupported", "unsupport", "no support",
        "not implement", "not allowed", "not permitted", "invalid", "unexpected",
        "unrecognized", "unknown", "extra", "must not", "cannot", "can not",
        "不支持", "未支持", "非法", "无效", "不允许", "未知")


def _neg(d: str) -> bool:
    return any(k in d for k in _NEG)


def is_tools_unsupported(detail: str) -> bool:
    d = (detail or "").lower()
    if not d:
        return False
    # Qwen3 / Qwen3.5 等模型的 chat_template.jinja 在 multi_step_tool 模式下
    # （即请求里带了 tools 时）会直接 `raise_exception('No user query found in
    # messages.')`，与「消息里到底有没有 user」无关。这是模型模板缺陷，而非
    # 兼容画像问题；上游 vLLM 会原样把该异常透传为 HTTP 400。唯一可行的自愈动作
    # 就是去掉 tools 后重发（详见 vllm-project/vllm#36432）。
    # 注意：该错误文本不含 "tool" 关键字，必须单独识别，否则会被 diagnose 漏判为
    # 永久错误而直接掐断回退链。
    if "no user query found in messages" in d:
        return True
    if not any(k in d for k in ("tool", "function call", "function_call",
                                "工具", "函数调用")):
        return False
    if "chat template" in d or "no tool parser" in d or "tool parser" in d:
        return True
    return _neg(d)


def is_param_unsupported(detail: str) -> bool:
    d = (detail or "").lower()
    if not d:
        return False
    return any(k in d for k in (
        "extra inputs are not permitted", "extra fields not permitted",
        "unexpected keyword argument", "unrecognized request argument",
        "additional properties are not allowed", "additionalproperties",
        "unknown field", "unknown argument", "unknown parameter",
        "unsupported parameter", "stream_options", "parallel_tool_calls",
        "不支持的参数", "未知参数", "多余的参数",
    ))


def is_content_invalid(detail: str) -> bool:
    d = (detail or "").lower()
    if not d:
        return False
    if not any(k in d for k in ("content", "内容", "messages")):
        return False
    return any(k in d for k in (
        "none is not an allowed value", "none is not", "not a valid string",
        "str type expected", "input should be a valid string", "must be a string",
        "expected string", "is not of type", "should be a valid list",
        "cannot be null", "不能为空", "不能为 null", "必须为字符串",
    ))


def is_role_sequence_invalid(detail: str) -> bool:
    d = (detail or "").lower()
    if not d:
        return False
    return any(k in d for k in (
        "alternat", "roles must", "conversation roles", "must be user",
        "last message must", "should be user", "role sequence",
        "交替", "轮流", "必须以",
    ))


def is_system_position_invalid(detail: str) -> bool:
    d = (detail or "").lower()
    if not d:
        return False
    return "system" in d and any(k in d for k in (
        "beginning", "first", "start", "only one", "开头", "首位", "第一条",
    ))


def is_max_tokens_invalid(detail: str) -> bool:
    d = (detail or "").lower()
    if not d:
        return False
    if not any(k in d for k in ("max_new_tokens", "max_tokens", "maximum tokens",
                                "max output", "output length", "生成长度")):
        return False
    return any(k in d for k in ("less than", "less or equal", "must be", "exceed",
                                "greater than", "too large", "invalid", "at most",
                                "超过", "至多", "上限"))


def diagnose(status: int, detail: str) -> Optional[str]:
    """把上游错误映射为可执行的修复动作；无法自愈时返回 None。

    注意：调用方应**先**判定「上下文超长」（那条路径要裁历史，不在本函数职责内）。
    顺序有讲究 —— 先判定语义最明确的（tools / 角色 / system），再判定宽泛的参数类。
    """
    if status not in (400, 404, 422, 500, 501):
        return None
    d = (detail or "").strip()
    if not d:
        return None
    if is_tools_unsupported(d):
        return REPAIR_DROP_TOOLS
    if is_system_position_invalid(d):
        return REPAIR_SYSTEM
    if is_role_sequence_invalid(d):
        return REPAIR_ALTERNATE
    if is_content_invalid(d):
        return REPAIR_PLAIN_CONTENT
    if is_max_tokens_invalid(d):
        return REPAIR_SHRINK_TOKENS
    if is_param_unsupported(d):
        return REPAIR_STRIP_OPTIONAL
    return None


def apply_repair(repair: str, profile: CompatProfile) -> CompatProfile:
    """把修复动作作用到画像上，得到「更保守」的新画像。"""
    if repair == REPAIR_DROP_TOOLS:
        return replace(profile, supports_tools=False)
    if repair == REPAIR_STRIP_OPTIONAL:
        return replace(
            profile,
            supports_stream_options=False, supports_parallel_tool_calls=False,
            drop_params=tuple(set(profile.drop_params) | {
                "stream_options", "parallel_tool_calls", "logprobs",
                "top_logprobs", "response_format", "seed", "n",
            }),
        )
    if repair == REPAIR_PLAIN_CONTENT:
        return replace(profile, multimodal_content=False)
    if repair == REPAIR_ALTERNATE:
        return replace(profile, require_alternating_roles=True)
    if repair == REPAIR_SYSTEM:
        return replace(profile, system_at_beginning=True)
    return profile


def remember(pc: Any, model: str, repair: str) -> None:
    """把成功的修复动作写入运行期能力缓存，后续请求直接生效。"""
    cap = REPAIR_TO_CAP.get(repair)
    if not cap:
        return
    runtime_caps.add(RuntimeCaps.key(getattr(pc, "base_url", ""), model), cap)
