"""多推理框架兼容层（vLLM / SGLang / LMDeploy / MindIE / Ollama ...）回归测试。

覆盖：
  - detect_compat / resolve_profile / 别名解析
  - sanitize_messages：content:null→""、多模态拍平、不支持 tools 时摊平工具轮次
  - sanitize_kwargs：按画像剔除 stream_options / parallel_tool_calls / tools
  - diagnose：把上游错误文案映射为修复动作
  - _chat_once：不支持 function calling 时自动去 tools 重试（且不死循环）、
                content 非法时降级重试、修复成功后写入运行期能力缓存
  - _stream_once：不支持 tools 时同样能自愈
  - effective_profile：叠加运行期学到的结论

运行：PYTHONPATH=backend python backend/tests/test_multiframework_compat.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest.mock as mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import httpx
import pytest

from zhishu.core.providers import compat
from zhishu.core.providers.client import LLMClient


def _pc(base_url="http://x:8000/v1", compat_val=""):
    return types.SimpleNamespace(
        name="test", base_url=base_url, api_key=None,
        auth_header="Authorization", auth_prefix="Bearer", compat=compat_val,
    )


class _FakeResp:
    def __init__(self, status, payload, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=types.SimpleNamespace(), response=self)


def setup_function(_):
    compat.runtime_caps.clear()


# ---------------------------------------------------------------- 探测 / 解析
def test_detect_by_port_and_keyword():
    assert compat.detect_compat("http://h:11434/v1") == "ollama"
    assert compat.detect_compat("http://h:23333/v1") == "lmdeploy"
    assert compat.detect_compat("http://h:30000/v1") == "sglang"
    assert compat.detect_compat("http://h:9997/v1") == "xinference"
    assert compat.detect_compat("http://vllm-svc:8000/v1") == "vllm"
    assert compat.detect_compat("http://ascend-npu:1025/v1") == "mindie"
    assert compat.detect_compat("https://dashscope.aliyuncs.com/compatible-mode/v1") == "openai"
    # 认不出 → generic
    assert compat.detect_compat("http://10.0.0.9:8010/v1") == "generic"


def test_resolve_profile_aliases_and_case():
    assert compat.resolve_profile("VLLM").key == "vllm"
    assert compat.resolve_profile("turbomind").key == "lmdeploy"
    assert compat.resolve_profile("昇腾").key == "mindie"
    assert compat.resolve_profile("sgl").key == "sglang"
    # 空 → 自动探测
    assert compat.resolve_profile("", "http://h:11434/v1").key == "ollama"
    # 未知值 → 回退自动探测（此处认不出 → generic）
    assert compat.resolve_profile("nonsense", "http://h:1/v1").key == "generic"


def test_profile_options_shape():
    opts = compat.profile_options()
    assert opts[0]["value"] == ""  # 首项为「自动探测」
    keys = {o["value"] for o in opts}
    for k in ("vllm", "sglang", "lmdeploy", "mindie", "ollama"):
        assert k in keys


# ---------------------------------------------------------------- 消息规整
def test_sanitize_content_null_to_empty():
    prof = compat.PROFILES["lmdeploy"]
    msgs = [{"role": "assistant", "content": None,
             "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]}]
    out = compat.sanitize_messages(msgs, prof, tools_enabled=True)
    assert out[0]["content"] == ""   # None → "" 满足严格 pydantic


def test_sanitize_flatten_multimodal_for_mindie():
    prof = compat.PROFILES["mindie"]
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "http://img"}},
    ]}]
    out = compat.sanitize_messages(msgs, prof, tools_enabled=True)
    assert isinstance(out[0]["content"], str)
    assert "看这张图" in out[0]["content"] and "[图片]" in out[0]["content"]


def test_flatten_tool_messages_when_tools_unsupported():
    prof = compat.PROFILES["mindie"]
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "1", "function": {"name": "search", "arguments": "{\"q\":1}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "命中3条"},
    ]
    # supports_tools False（模拟已探明不支持）
    p2 = compat.apply_repair(compat.REPAIR_DROP_TOOLS, prof)
    out = compat.sanitize_messages(msgs, p2, tools_enabled=False)
    roles = [m["role"] for m in out]
    assert "tool" not in roles                       # role=tool 已摊平
    assert all(not m.get("tool_calls") for m in out)  # tool_calls 已摊平
    joined = " ".join(m["content"] for m in out)
    assert "search" in joined and "命中3条" in joined


def test_merge_system_at_beginning():
    prof = compat.PROFILES["vllm"]
    msgs = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "mid"},
    ]
    out = compat.sanitize_messages(msgs, prof, tools_enabled=True)
    assert out[0]["role"] == "system"
    assert "base" in out[0]["content"] and "mid" in out[0]["content"]
    assert len([m for m in out if m["role"] == "system"]) == 1


def test_enforce_alternating_roles():
    prof = compat.apply_repair(compat.REPAIR_ALTERNATE, compat.PROFILES["generic"])
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},        # 连续 user → 合并
        {"role": "assistant", "content": "c"},
    ]
    out = compat.sanitize_messages(msgs, prof, tools_enabled=True)
    body = [m for m in out if m["role"] != "system"]
    roles = [m["role"] for m in body]
    assert roles == ["user", "assistant"]
    assert "a" in body[0]["content"] and "b" in body[0]["content"]


# ---------------------------------------------------------------- 请求体规整
def test_sanitize_kwargs_drops_optional_for_lmdeploy():
    prof = compat.PROFILES["lmdeploy"]
    kw = {"messages": [], "stream_options": {"include_usage": True},
          "parallel_tool_calls": True, "temperature": 0.7}
    out = compat.sanitize_kwargs(kw, prof, tools_enabled=True)
    assert "stream_options" not in out
    assert "parallel_tool_calls" not in out
    assert out["temperature"] == 0.7


def test_sanitize_kwargs_keeps_stream_options_for_vllm():
    prof = compat.PROFILES["vllm"]
    kw = {"messages": [], "stream_options": {"include_usage": True}}
    out = compat.sanitize_kwargs(kw, prof, tools_enabled=True)
    assert "stream_options" in out


def test_sanitize_kwargs_drops_tools_when_disabled():
    prof = compat.apply_repair(compat.REPAIR_DROP_TOOLS, compat.PROFILES["ollama"])
    kw = {"messages": [], "tools": [{"type": "function"}], "tool_choice": "auto"}
    out = compat.sanitize_kwargs(kw, prof, tools_enabled=False)
    assert "tools" not in out and "tool_choice" not in out


# ---------------------------------------------------------------- 诊断
def test_diagnose_maps_errors():
    d = compat.diagnose
    assert d(400, "This model does not support tools") == compat.REPAIR_DROP_TOOLS
    assert d(400, "No tool parser configured, tools is not supported") == compat.REPAIR_DROP_TOOLS
    assert d(400, "Extra inputs are not permitted: stream_options") == compat.REPAIR_STRIP_OPTIONAL
    assert d(400, "content: none is not an allowed value") == compat.REPAIR_PLAIN_CONTENT
    assert d(400, "conversation roles must alternate user/assistant") == compat.REPAIR_ALTERNATE
    assert d(500, "system message must be at the beginning") == compat.REPAIR_SYSTEM
    assert d(400, "max_new_tokens must be less than 8192") == compat.REPAIR_SHRINK_TOKENS
    # Qwen3 / Qwen3.5 chat_template 缺陷：带 tools 时直接 400
    # "No user query found in messages."（文本不含 "tool" 关键字，历史上被漏判
    # → 直接掐断回退链让用户看到「所有 Provider 均不可用」）。必须识别为
    # 「不支持 function calling」并去掉 tools 重试。
    assert d(400, "No user query found in messages.") == compat.REPAIR_DROP_TOOLS
    assert d(400, "No user query found in messages: [{'role': 'system', ...}]") == compat.REPAIR_DROP_TOOLS
    # 与兼容无关的错误 → 不自愈
    assert d(400, "model 'foo' not found") is None
    assert d(401, "invalid api key") is None
    assert d(400, "") is None


# ---------------------------------------------------------------- 调用级自愈
def test_chat_once_degrades_when_tools_unsupported():
    """不支持 function calling 的端点：第 1 次带 tools 400 → 去掉 tools 重试成功，
    且历史里的 tool_calls / role=tool 被摊平；不会死循环。"""
    pc = _pc(base_url="http://h:23333/v1")  # lmdeploy
    client = LLMClient(cfg=None, api_mode="openai")
    err = _FakeResp(400, {"error": {"message": "tools is not supported by this model"}})
    ok = _FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": "done"}}]})
    calls = []

    async def fake_post(url, json=None, headers=None):
        calls.append(json)
        return err if len(calls) == 1 else ok

    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    msgs = [{"role": "user", "content": "hi"}]
    with mock.patch("zhishu.core.providers.client.get_shared_http",
                    return_value=types.SimpleNamespace(post=fake_post)):
        out = asyncio.run(client._chat_once(pc, "m", msgs, tools, 0.7, 2048))
    assert out["choices"][0]["message"]["content"] == "done"
    assert len(calls) == 2
    assert "tools" in calls[0]              # 首次带 tools
    assert "tools" not in calls[1]          # 重试去掉 tools
    # 结论已记住：同端点下次直接不带 tools
    assert compat.runtime_caps.has(
        compat.RuntimeCaps.key("http://h:23333/v1", "m"), compat.CAP_NO_TOOLS)


def test_chat_once_degrades_qwen35_template_error():
    """Qwen3.5 chat_template 缺陷：带 tools 的请求直接 400 'No user query found
    in messages.'（文本不含 tool 关键字）。必须识别为「不支持 function calling」并
    去掉 tools 重试；历史里的 tool_calls / role=tool 也要摊平，且不死循环。"""
    pc = _pc(base_url="http://qwen3:8000/v1", compat_val="vllm")
    client = LLMClient(cfg=None, api_mode="openai")
    err = _FakeResp(400, {"error": {"message": "No user query found in messages."}})
    ok = _FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": "done"}}]})
    calls = []

    async def fake_post(url, json=None, headers=None):
        calls.append(json)
        return err if len(calls) == 1 else ok

    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    # 含一段工具轮次历史：去掉 tools 重试时必须把 tool_calls / role=tool 摊平
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "r"},
        {"role": "user", "content": "再问一次"},
    ]
    with mock.patch("zhishu.core.providers.client.get_shared_http",
                    return_value=types.SimpleNamespace(post=fake_post)):
        out = asyncio.run(client._chat_once(pc, "m", msgs, tools, 0.7, 2048))
    assert out["choices"][0]["message"]["content"] == "done"
    assert len(calls) == 2
    assert "tools" in calls[0]              # 首次带 tools
    assert "tools" not in calls[1]          # 重试去掉 tools
    assert all(not m.get("tool_calls") for m in calls[1]["messages"])
    assert all(m["role"] != "tool" for m in calls[1]["messages"])
    # 结论已记住：同端点下次直接不带 tools
    assert compat.runtime_caps.has(
        compat.RuntimeCaps.key("http://qwen3:8000/v1", "m"), compat.CAP_NO_TOOLS)


def test_chat_once_no_infinite_loop_on_persistent_tools_error():
    """端点持续报 tools 不支持：去 tools 后仍 400（另一个非兼容原因）→ 有限次后失败。"""
    pc = _pc(base_url="http://h:23333/v1")
    client = LLMClient(cfg=None, api_mode="openai")
    calls = []

    async def fake_post(url, json=None, headers=None):
        calls.append(json)
        # 第 1 次报 tools 不支持；去 tools 后改报另一个无法自愈的 400
        if len(calls) == 1:
            return _FakeResp(400, {"error": {"message": "tools is not supported"}})
        return _FakeResp(400, {"error": {"message": "model 'x' not found"}})

    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    with mock.patch("zhishu.core.providers.client.get_shared_http",
                    return_value=types.SimpleNamespace(post=fake_post)):
        with pytest.raises(RuntimeError) as ei:
            asyncio.run(client._chat_once(pc, "m", [{"role": "user", "content": "hi"}],
                                          tools, 0.7, 2048))
    # 去 tools 重试一次即撞上不可自愈错误 → 停止，不无限循环
    assert len(calls) == 2
    assert "not found" in str(ei.value)


def test_chat_once_repairs_content_invalid():
    pc = _pc(base_url="http://h:1025/v1")  # mindie
    client = LLMClient(cfg=None, api_mode="openai")
    calls = []

    async def fake_post(url, json=None, headers=None):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResp(400, {"error": {"message":
                "messages.1.content: none is not an allowed value"}})
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    msgs = [{"role": "user", "content": "hi"}]
    with mock.patch("zhishu.core.providers.client.get_shared_http",
                    return_value=types.SimpleNamespace(post=fake_post)):
        out = asyncio.run(client._chat_once(pc, "m", msgs, None, 0.7, 2048))
    assert out["choices"][0]["message"]["content"] == "ok"
    assert len(calls) == 2


def test_stream_once_degrades_when_tools_unsupported():
    pc = _pc(base_url="http://h:11434/v1")  # ollama
    client = LLMClient(cfg=None, api_mode="openai")
    calls = []

    class _StreamCtx:
        def __init__(self, status, lines, payload=None):
            self.status_code = status
            self._lines = lines
            self._payload = payload or {}
            self.text = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("e", request=types.SimpleNamespace(), response=self)

        async def aread(self):
            return b""

        def json(self):
            return self._payload

        async def aiter_lines(self):
            for ln in self._lines:
                yield ln

    def fake_stream(method, url, json=None, headers=None):
        calls.append(json)
        if len(calls) == 1:
            return _StreamCtx(400, [], {"error": {"message": "tools is not supported"}})
        return _StreamCtx(200, ['data: {"choices":[{"delta":{"content":"hello"}}]}',
                                'data: [DONE]'])

    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    fake_http = types.SimpleNamespace(stream=fake_stream)

    async def run():
        got = []
        with mock.patch("zhishu.core.providers.client.get_shared_http", return_value=fake_http):
            async for piece in client._stream_once(pc, "m", [{"role": "user", "content": "hi"}],
                                                    tools, 0.7, 2048):
                got.append(piece)
        return got

    got = asyncio.run(run())
    assert "".join(got) == "hello"
    assert len(calls) == 2
    assert "tools" in calls[0] and "tools" not in calls[1]


# ---------------------------------------------------------------- 运行期缓存
def test_effective_profile_applies_runtime_caps():
    pc = _pc(base_url="http://h:8000/v1", compat_val="vllm")
    base = compat.effective_profile(pc, "m")
    assert base.supports_tools is None  # vllm 默认未知
    compat.runtime_caps.add(compat.RuntimeCaps.key("http://h:8000/v1", "m"),
                            compat.CAP_NO_TOOLS)
    after = compat.effective_profile(pc, "m")
    assert after.supports_tools is False  # 学到「不支持 tools」后固化


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
