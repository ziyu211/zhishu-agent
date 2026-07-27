"""智枢智能体 —— Provider 传输层注册表（对标 Hermes `agent/transports/__init__.py`）。

  按 api_mode 注册/查找传输层实现，并支持自动发现（导入本包即触发各 transport 模块
  的自注册）。
"""
from __future__ import annotations

from .base import ProviderTransport, OpenAICompatibleTransport


_REGISTRY: dict[str, type[ProviderTransport]] = {}
_DISCOVERED = False


def register_transport(api_mode: str, cls: type[ProviderTransport]) -> None:
    """注册一个传输层实现到指定 api_mode。"""
    _REGISTRY[api_mode] = cls


def get_transport(api_mode: str) -> ProviderTransport:
    """根据 api_mode 取得传输层实例；未知模式回退到 OpenAI 兼容（直通）。"""
    _discover()
    cls = _REGISTRY.get(api_mode) or _REGISTRY.get("openai") or OpenAICompatFallback
    return cls()


def available_transports() -> list[str]:
    _discover()
    return sorted(_REGISTRY.keys())


def _discover() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True
    # 注册内置实现
    register_transport("openai", OpenAICompatibleTransport)
    # 自动发现：导入已知 transport 模块以触发其自注册
    import importlib

    for mod in ("anthropic", "bedrock", "gemini"):
        try:
            importlib.import_module(f"{__name__.rsplit('.', 1)[0]}.{mod}")
        except Exception:
            # 该 transport 尚未实现或依赖缺失：静默跳过，不影响主流程
            pass


class OpenAICompatFallback(OpenAICompatibleTransport):
    """兜底：任何未知 api_mode 都按 OpenAI 兼容处理。"""
