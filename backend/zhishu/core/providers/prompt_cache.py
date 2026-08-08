"""Provider 门控的 Prompt 缓存（对标 Hermes ``agent/prompt_caching.py``）。

核心思想：把「稳定前缀（身份 / 指令 / 工具定义）」与「易变内容（检索结果、当前轮
输入）」用 ``cache_control`` 断点隔开，使 Provider 的 KV 前缀缓存能够命中，从而缩短
多步推理时每一轮都要重发的稳定前缀的预处理耗时。

Hermes 用 4 个 ``cache_control`` 断点 + 字节稳定前缀实现；智枢的 ``run()`` 在单轮内
只构建一次 system 提示词、且 RAG/长期记忆上下文基于同轮恒定的 ``user_message``，
因此**单轮多步推理的前缀天然稳定**，缓存收益最大（这正是智枢「比 Hermes 慢」的主因之一）。

各 Provider 家族策略（``auto`` 模式）：
- ``anthropic`` / ``claude``：在 system 末块 + 末个 tool 上挂 ``cache_control``。
- ``deepseek``：支持 ``cache_control`` 标记，同时置 ``prompt_cache=true``。
- ``qwen`` / ``dashscope`` / ``aliyun``：通过 ``extra_body.prompt_cache`` 开启。
- ``openai`` / ``azure`` / ``moonshot`` / ``kimi`` / 未知：服务端自动前缀缓存
  （≥1024 tokens 的稳定前缀即命中），**不注入任何标记**以免严格端点 400。
- ``ollama`` / ``vllm``：服务端自管 KV 缓存，不注入标记。

所有注入均在 ``sanitize_kwargs`` 之后进行，不会被兼容层剥除；纯函数、无网络副作用。
"""
from __future__ import annotations

__all__ = ["provider_family", "apply_prompt_cache"]

_CACHE_CONTROL = {"type": "ephemeral"}


def provider_family(pc) -> str:
    """从 Provider 名称 / base_url 推断家族。``pc`` 只需有 ``name`` / ``base_url`` 属性。"""
    name = (getattr(pc, "name", "") or "").lower()
    url = (getattr(pc, "base_url", "") or "").lower()
    hay = f"{name} {url}"
    if "anthropic" in hay or "claude" in hay:
        return "anthropic"
    if "deepseek" in hay:
        return "deepseek"
    if "dashscope" in hay or "qwen" in hay or "aliyun" in hay:
        return "qwen"
    if "ollama" in hay:
        return "ollama"
    if "vllm" in hay:
        return "vllm"
    if "azure" in hay or "openai" in hay or "moonshot" in hay or "kimi" in hay:
        return "openai"
    # 回环地址（本地推理端点 Ollama / vLLM 等）：服务端自管 KV 缓存，跳过注入。
    if "127.0.0.1" in hay or "localhost" in hay or "0.0.0.0" in hay or "::1" in hay:
        return "local"
    return "unknown"


def _as_block_content(content):
    """把 system content 规整成 OpenAI 内容块列表，便于挂 ``cache_control``。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        out = []
        for blk in content:
            out.append(dict(blk) if isinstance(blk, dict) else {"type": "text", "text": str(blk)})
        return out
    return [{"type": "text", "text": str(content)}]


def _inject_anthropic_style(kw: dict) -> dict:
    """在 system 末块 + 末个 tool 上挂 ``cache_control`` 断点（Anthropic / DeepSeek / 强制模式）。"""
    msgs = kw.get("messages") or []
    for i, m in enumerate(msgs):
        if m.get("role") == "system":
            blocks = _as_block_content(m.get("content"))
            if blocks:
                blocks[-1] = {**blocks[-1], "cache_control": dict(_CACHE_CONTROL)}
            msgs[i] = {"role": "system", "content": blocks}
            break
    tools = kw.get("tools")
    if isinstance(tools, list) and tools:
        tools = list(tools)
        last = dict(tools[-1])
        last["cache_control"] = dict(_CACHE_CONTROL)
        tools[-1] = last
        kw["tools"] = tools
    return kw


def _inject_qwen(kw: dict) -> dict:
    eb = dict(kw.get("extra_body") or {})
    eb["prompt_cache"] = True
    kw["extra_body"] = eb
    return kw


def _inject_deepseek(kw: dict) -> dict:
    kw = _inject_anthropic_style(kw)
    kw["prompt_cache"] = True
    return kw


def apply_prompt_cache(kw: dict, pc, mode: str) -> dict:
    """在已 sanitize 的请求体上按 Provider 家族注入缓存标记。``kw`` 为 None / ``off`` 时原样返回。"""
    if not kw or mode in ("off", None):
        return kw
    fam = provider_family(pc)
    if mode == "auto":
        if fam in ("ollama", "vllm", "local"):
            return kw
        if fam == "anthropic":
            return _inject_anthropic_style(kw)
        if fam == "deepseek":
            return _inject_deepseek(kw)
        if fam == "qwen":
            return _inject_qwen(kw)
        # openai / azure / moonshot / kimi / unknown：服务端自动前缀缓存，不注入标记（避免 400）
        return kw
    if mode == "force":
        # 专家模式：对所有 Provider 按 Anthropic 风格注入（适用于明确支持 cache_control 的网关）
        return _inject_anthropic_style(kw)
    return kw
