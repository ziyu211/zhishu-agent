"""智枢智能体 —— Provider 传输层抽象（对标 Hermes `agent/transports/base.py`）。

设计意图：
  Hermes 把「消息/工具格式转换」与「SDK 客户端构建」解耦为两层：
    api_mode  ->  ProviderTransport（格式转换 + 响应归一化）
              ->  *_adapter.py（provider 客户端构建 + 厂商怪癖归一化）

  智枢当前所有内置 Provider 均为 **OpenAI 兼容** 协议，故默认传输层为直通
  （identity）。当接入 Anthropic / Bedrock / Gemini 等差异协议时，只需新增一个
  Transport 子类实现 convert_*/normalize_*，无需改动 LLMClient 主流程。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderTransport(ABC):
    """Provider 传输层：负责与具体协议相关的格式转换。

    默认 api_mode="openai"，所有方法为直通（不改变数据）。
    """

    api_mode: str = "openai"

    def convert_messages(self, messages: list[dict]) -> list[dict]:
        """把内部统一消息格式转换为该 Provider 期望的格式。"""
        return messages

    def convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """把 OpenAI function-calling 工具声明转换为该 Provider 期望的格式。"""
        return tools

    def build_kwargs(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
        **extra: Any,
    ) -> dict:
        """组装该 Provider 的 chat 请求体。"""
        kw: dict[str, Any] = {
            "messages": self.convert_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            kw["tools"] = self.convert_tools(tools)
            kw["tool_choice"] = "auto"
        kw.update(extra)
        return kw

    def normalize_response(self, resp: dict) -> dict:
        """把该 Provider 的原始响应归一化为 OpenAI 兼容的 dict。"""
        return resp


class OpenAICompatibleTransport(ProviderTransport):
    """OpenAI 兼容协议（智枢全部内置 Provider 默认）。直通实现。"""

    api_mode = "openai"
