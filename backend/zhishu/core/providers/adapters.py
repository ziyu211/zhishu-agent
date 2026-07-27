"""智枢智能体 —— Provider 适配器（对标 Hermes `agent/*_adapter.py`）。

  Hermes 的 adapter 负责「识别第三方网关 + 构建客户端 + 厂商怪癖归一化」。
  智枢内置 Provider 均为 OpenAI 兼容端点，故默认适配器即返回通用 LLMClient；
  但保留 api_mode 探测与适配器注册表，便于接入讲不同协议的网关
  （如某些第三方把 Anthropic Messages 格式代理到 OpenAI 兼容网关）。
"""
from __future__ import annotations

from typing import Callable

from ..config import ProviderConfig, ZhishuConfig


# 适配器注册表：api_mode -> 客户端构建函数 (pc, cfg) -> 可调用 chat/stream 的对象
_ADAPTERS: dict[str, Callable] = {}


def register_adapter(api_mode: str, factory: Callable) -> None:
    _ADAPTERS[api_mode] = factory


def detect_api_mode(pc: ProviderConfig) -> str:
    """根据 base_url / 名称 推断该 Provider 走哪条传输协议。

    默认返回 "openai"（智枢全部内置 Provider 均为 OpenAI 兼容）。
    若将来新增讲 Anthropic/Bedrock 协议的网关，可在此扩展匹配规则。
    """
    url = (pc.base_url or "").lower()
    name = (pc.name or "").lower()
    if "anthropic" in url or "claude" in name:
        return "anthropic"
    if "bedrock" in url or "bedrock" in name:
        return "bedrock"
    return "openai"


def build_provider_client(pc: ProviderConfig, cfg: ZhishuConfig):
    """构建该 Provider 的客户端（LLMClient 兼容接口）。

    默认返回通用 LLMClient；若注册了对应 api_mode 的适配器则优先使用。
    """
    mode = detect_api_mode(pc)
    if mode in _ADAPTERS:
        return _ADAPTERS[mode](pc, cfg)
    # 延迟导入避免循环：LLMClient 定义在 client 模块
    from .client import LLMClient

    return LLMClient(cfg, api_mode=mode)
