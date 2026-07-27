"""智枢智能体 —— Provider 分层抽象包（对标 Hermes `agent/transports` + `*_adapter.py`）。

  该包把「LLM 客户端」拆成两层：
    * Transport  （格式转换 + 响应归一化，按 api_mode 选择）
    * Adapter    （按 provider 构建客户端，识别第三方网关）
    * Client     （统一的 chat/stream/embed/image/video 实现，内置回退链）
"""
from .base import ProviderTransport, OpenAICompatibleTransport
from .registry import register_transport, get_transport, available_transports
from .adapters import register_adapter, detect_api_mode, build_provider_client
from .client import LLMClient

__all__ = [
    "ProviderTransport",
    "OpenAICompatibleTransport",
    "register_transport",
    "get_transport",
    "available_transports",
    "register_adapter",
    "detect_api_mode",
    "build_provider_client",
    "LLMClient",
]
