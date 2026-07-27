"""智枢智能体 —— MoA 多智能体 facade（对标 Hermes `agent/moa_loop.py`）。

  把「多 Agent 协作」伪装成一个 LLM client：对外暴露与 LLMClient 一致的
  chat/stream 接口，内部并行跑 N 个 reference agent，再把它们的回答聚合为
  最终输出。复用整个 ReAct 循环，无需为 MoA 单独写一套流程。

  启用方式（opt-in，默认关闭）：在 provider 配置中把某个 provider 设为
  mode="moa"，或在 cfg.agent 中配置 moa_reference_models。Agent.run 检测到
  moa 模式的 provider 时，会把本轮对话路由到本 MoAClient。
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from ..config import ZhishuConfig
from ...context import get_ctx
from ..tools import ToolRegistry, ToolContext
from ..agents_runtime import DELEGATE_TOOL_NAME


class MoAClient:
    def __init__(self, cfg: ZhishuConfig):
        self.cfg = cfg

    # --------------------------- 公共接口（与 LLMClient 对齐）---------------------------
    async def chat(self, messages, model=None, tools=None,
                   temperature=0.7, max_tokens=2048) -> dict:
        refs = await self._run_references(messages)
        aggregated = await self._aggregate(refs)
        return {"choices": [{"message": {"role": "assistant", "content": aggregated}}]}

    async def stream(self, messages, model=None, tools=None,
                     temperature=0.7, max_tokens=2048) -> AsyncIterator[str]:
        refs = await self._run_references(messages)
        aggregated = await self._aggregate(refs)
        # 简单按句切分，模拟流式
        for piece in _chunk_text(aggregated):
            yield piece

    # --------------------------- 内部 ---------------------------
    def _reference_models(self) -> list[str]:
        cfg = self.cfg
        if getattr(cfg.agent, "moa_reference_models", None):
            return list(cfg.agent.moa_reference_models)
        # 兜底：取最多 3 个启用 provider 的首模型
        out: list[str] = []
        for p in cfg.ordered_providers():
            if p.models:
                out.append(f"{p.name}/{p.models[0]}")
            if len(out) >= 3:
                break
        return out

    def _aggregator_model(self) -> str:
        return getattr(self.cfg.agent, "moa_aggregator", "") or self.cfg.default_model

    async def _run_references(self, messages) -> list[str]:
        models = self._reference_models()
        if not models:
            return []
        results = await asyncio.gather(
            *(self._run_reference(m, messages) for m in models),
            return_exceptions=True,
        )
        out = []
        for r in results:
            if isinstance(r, Exception):
                continue
            if r:
                out.append(r)
        return out

    async def _run_reference(self, model: str, messages) -> str:
        g = get_ctx()
        llm = g.llm
        ctx = ToolContext(kb=g.kb, security=g.cfg.security,
                          user="moa", session="moa")
        # 禁用委派，避免 reference agent 再派生子 agent 形成递归
        ref_specs = [s for s in ToolRegistry.specs()
                     if s["function"]["name"] != DELEGATE_TOOL_NAME]
        sys_prompt = g.cfg.system_prompt
        msgs = [{"role": "system", "content": sys_prompt}] + list(messages)

        resp = await llm.chat(msgs, model=model, tools=ref_specs if ref_specs else None)
        msg = resp["choices"][0]["message"]
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except json.JSONDecodeError:
                    args = {}
                res = await ToolRegistry.execute(fn.get("name", ""), args, ctx)
                msgs.append({"role": "assistant", "content": msg.get("content"),
                             "tool_calls": msg["tool_calls"]})
                msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "content": res})
            resp2 = await llm.chat(msgs, model=model, tools=None)
            return resp2["choices"][0]["message"].get("content", "")
        return msg.get("content", "")

    async def _aggregate(self, references: list[str]) -> str:
        if not references:
            return "（MoA：无可用的参考模型，请检查 moa_reference_models 配置）"
        g = get_ctx()
        llm = g.llm
        joined = "\n\n".join(f"专家{i+1}：\n{r}" for i, r in enumerate(references))
        prompt = (
            "你是严谨的答案聚合器。下面有多位专家对同一问题的回答，"
            "请综合它们，去重、纠错，给出最终最佳回答（保持中文）：\n\n" + joined
        )
        try:
            resp = await llm.chat(
                [{"role": "system", "content": "你是答案聚合器，输出最终综合回答。"},
                 {"role": "user", "content": prompt}],
                model=self._aggregator_model(),
            )
            return resp["choices"][0]["message"].get("content", "")
        except Exception as e:
            return f"（聚合失败：{e}）\n\n" + joined


def _chunk_text(text: str, size: int = 24) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [text]
