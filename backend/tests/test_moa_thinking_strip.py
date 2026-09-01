"""MoA 多智能体 facade 思考链泄漏回归（v1.0.57 · 修复 C 收口）。

MoAClient 直接聚合各 reference / aggregator 模型的 content；若底层 provider
为内网 qwen3.5 / sensenova 等 reasoning 模型，thinking 会混进 content，
经聚合后随流式 token 泄漏给用户。修复点在 MoAClient.chat / stream 内对
聚合结果整体 strip_thinking。本文件绕过真实 LLM，直接注入含思考链的聚合文本
验证剥离是否生效。
"""
import asyncio
from types import SimpleNamespace

from zhishu.core.agent.moa import MoAClient


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _patch(client, thinking_text):
    """用假聚合结果替换真实 LLM 调用，仅验证剥离逻辑。"""

    async def fake_run_references(messages):
        return []

    async def fake_aggregate(refs):
        return thinking_text

    client._run_references = fake_run_references
    client._aggregate = fake_aggregate


def test_moa_stream_strips_thinking():
    client = MoAClient(cfg=SimpleNamespace())
    _patch(
        client,
        "让我先想想步骤。<think>内部推理：应该调用 read_file 工具</think>"
        "最终答案在这里。",
    )

    async def collect():
        return "".join(
            [p async for p in client.stream([{"role": "user", "content": "hi"}])]
        )

    out = _run(collect())
    assert "<think>" not in out
    assert "内部推理" not in out
    assert "最终答案在这里" in out


def test_moa_stream_strips_orphan_close_think():
    client = MoAClient(cfg=SimpleNamespace())
    _patch(
        client,
        "思考过程：需要排版。<br/></think>\n\n正式回复开始。",
    )

    async def collect():
        return "".join(
            [p async for p in client.stream([{"role": "user", "content": "hi"}])]
        )

    out = _run(collect())
    assert "</think>" not in out
    assert "思考过程：需要排版" not in out
    assert "正式回复开始" in out


def test_moa_chat_strips_thinking():
    client = MoAClient(cfg=SimpleNamespace())
    _patch(client, "前言<reasoning>secret thought</reasoning>正文")

    async def go():
        return (await client.chat([{"role": "user", "content": "hi"}]))[
            "choices"
        ][0]["message"]["content"]

    out = _run(go())
    assert "<reasoning>" not in out
    assert "secret thought" not in out
    assert "正文" in out


def test_moa_stream_no_thinking_passthrough():
    """无思考链时不得破坏正常内容（含特殊符号）。"""
    client = MoAClient(cfg=SimpleNamespace())
    _patch(client, "正常回复：已完成排版，<b>加粗</b> 与 1 < 2 均保留。")

    async def collect():
        return "".join(
            [p async for p in client.stream([{"role": "user", "content": "hi"}])]
        )

    out = _run(collect())
    assert "已完成排版" in out
    assert "<b>加粗</b>" in out
    assert "1 < 2" in out
