"""上下文超长（HTTP 400）自动裁剪重试回归测试。

覆盖：
  - _is_context_overflow 对「上下文超长」类 400 命中、对「模型不存在/参数非法」不误判
  - _truncate_messages 保留 system、按完整轮次丢弃（不破坏 tool_calls 配对）、截断超长单条
  - _chat_once 遇到上下文超长 400 时自动裁剪并重试，第 2 次成功返回

运行：PYTHONPATH=backend python backend/tests/test_ctx_overflow_retry.py
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

from zhishu.core.providers.client import (
    LLMClient,
    _is_context_overflow,
    _truncate_messages,
    _normalize_system_messages,
    _CTX_TRUNCATE_RETRIES,
)


def _make_pc():
    return types.SimpleNamespace(
        name="test",
        base_url="http://x/v1",
        api_key=None,
        auth_header="Authorization",
        auth_prefix="Bearer",
    )


# ---------------------------------------------------------------- 超长检测
def test_is_context_overflow():
    assert _is_context_overflow(
        "This model's maximum context length is 131072 tokens. "
        "However, you requested 300000 tokens (...).")
    assert _is_context_overflow("ERR: max_model_len exceeded")
    assert _is_context_overflow("错误：超出上下文长度")
    # 不应误判其它 400
    assert not _is_context_overflow("")
    assert not _is_context_overflow("model 'foo' not found")
    assert not _is_context_overflow("Invalid 'tools' format")


# ---------------------------------------------------------------- 裁剪
def test_truncate_preserves_system_and_tool_pairs():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "content": "result1"},
        {"role": "user", "content": "q2-very-long-" + "x" * 5000},
    ]
    out = _truncate_messages(messages, 1)
    # system 保留
    assert out[0]["role"] == "system"
    # tool_calls 必须与其 tool 结果成对出现
    roles = [m["role"] for m in out]
    if "assistant" in roles:
        assert "tool" in roles
    # 超长单条被截断（无 >24000 字符的消息残留）
    assert not any(len(m.get("content", "")) > 24000 for m in out)
    # 最后一条用户消息保留
    assert any(m.get("content", "").startswith("q2-very-long") for m in out)


# ---------------------------------------------------------------- 调用级重试
class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=types.SimpleNamespace(), response=self)


def test_chat_once_retries_on_overflow():
    pc = _make_pc()
    client = LLMClient(cfg=None, api_mode="openai")

    overflow = _FakeResp(400, {"error": {
        "message": "This model's maximum context length is 131072 tokens. "
                   "However, you requested 300000 tokens (290000 in your prompt;"
                   " 10000 for the completion). Please reduce..."}})
    ok = _FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    state = {"n": 0}

    async def fake_post(url, json=None, headers=None):
        state["n"] += 1
        return overflow if state["n"] == 1 else ok

    fake_http = types.SimpleNamespace(post=fake_post)
    with mock.patch("zhishu.core.providers.client.get_shared_http", return_value=fake_http):
        out = asyncio.run(client._chat_once(pc, "m", [{"role": "user", "content": "x"}],
                                            None, 0.7, 2048))
    # 第 1 次 400 超长 -> 裁剪 -> 第 2 次成功，共 2 次调用
    assert state["n"] == 2
    assert out["choices"][0]["message"]["content"] == "ok"


def test_chat_once_no_retry_on_non_overflow_400():
    pc = _make_pc()
    client = LLMClient(cfg=None, api_mode="openai")
    notfound = _FakeResp(400, {"error": {"message": "model 'foo' not found"}})
    state = {"n": 0}

    async def fake_post(url, json=None, headers=None):
        state["n"] += 1
        return notfound

    fake_http = types.SimpleNamespace(post=fake_post)
    with mock.patch("zhishu.core.providers.client.get_shared_http", return_value=fake_http):
        with pytest.raises(RuntimeError) as ei:
            asyncio.run(client._chat_once(pc, "m", [{"role": "user", "content": "x"}],
                                         None, 0.7, 2048))
    assert state["n"] == 1  # 非超长 400 不重试，直接失败
    assert "model 'foo' not found" in str(ei.value)


# --------------------------- system 消息规范化 ---------------------------
def test_normalize_system_keeps_single_at_beginning():
    msgs = [{"role": "system", "content": "你是助手"},
            {"role": "user", "content": "hi"}]
    out = _normalize_system_messages(msgs)
    assert out == msgs  # 已合规，原样返回


def test_normalize_system_merges_mid_conversation():
    msgs = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "你好"},
        {"role": "system", "content": "[系统] 已创建团队，委协调者处理"},
        {"role": "user", "content": "开始分析"},
    ]
    out = _normalize_system_messages(msgs)
    # 1 条 system 在开头，内容合并；其余消息相对顺序不变
    assert out[0]["role"] == "system"
    assert "你是助手" in out[0]["content"]
    assert "[系统] 已创建团队" in out[0]["content"]
    assert [m["role"] for m in out[1:]] == ["user", "assistant", "user"]
    # 不破坏 tool_calls 配对：assistant(tool_calls) 紧随其后的 tool 结果保持相邻
    paired = [
        {"role": "system", "content": "base"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "r1"},
        {"role": "system", "content": "mid nudge"},
        {"role": "user", "content": "go"},
    ]
    out2 = _normalize_system_messages(paired)
    assert out2[0]["role"] == "system"
    assert out2[1]["role"] == "assistant" and out2[1].get("tool_calls")
    assert out2[2]["role"] == "tool"
    # assistant(tool_calls) 与其 tool 结果仍相邻
    assert out2.index(out2[1]) + 1 == out2.index(out2[2])


def test_normalize_system_no_system_passthrough():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert _normalize_system_messages(msgs) == msgs


def test_chat_once_sends_normalized_system_at_front():
    pc = _make_pc()
    client = LLMClient(cfg=None, api_mode="openai")
    captured = {}

    async def fake_post(url, json=None, headers=None):
        captured["json"] = json
        return _FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    fake_http = types.SimpleNamespace(post=fake_post)
    mid_sys_msgs = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "mid nudge"},
    ]
    with mock.patch("zhishu.core.providers.client.get_shared_http", return_value=fake_http):
        asyncio.run(client._chat_once(pc, "m", mid_sys_msgs, None, 0.7, 2048))
    sent = captured["json"]["messages"]
    assert sent[0]["role"] == "system"
    assert "base" in sent[0]["content"] and "mid nudge" in sent[0]["content"]
    assert len([m for m in sent if m["role"] == "system"]) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
