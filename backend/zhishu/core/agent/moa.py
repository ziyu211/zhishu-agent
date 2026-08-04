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
from ..tools.base import get_current_user, get_current_is_admin, get_current_role
from ..modules.runtime import filter_tool_specs
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
        # 在派发并发任务**之前**抓取身份快照，杜绝子任务读不到 contextvars 时
        # fail-closed 成 anonymous（会误伤本人私有工具）或继承错误身份。
        ident = (get_current_user(), get_current_is_admin(), get_current_role() or "")
        results = await asyncio.gather(
            *(self._run_reference(m, messages, ident) for m in models),
            return_exceptions=True,
        )
        out = []
        for r in results:
            if isinstance(r, Exception):
                continue
            if r:
                out.append(r)
        return out

    async def _run_reference(self, model: str, messages, ident=None) -> str:
        g = get_ctx()
        llm = g.llm
        # 多用户隔离：MoA 的 reference agent 必须继承**发起本轮请求的真实用户身份**，
        # 绝不能用 user="moa" 这类伪身份 —— 否则工具执行时 owner 判定失真，
        # A 用户通过 MoA 就能读到 B 用户的私有插件 / MCP / 知识库。
        # asyncio.gather 派生的子任务会复制 contextvars，但为稳妥起见由调用方
        # 在派发前抓取快照（ident）传入，避免任何上下文丢失导致 fail-open。
        owner, is_admin, user_role = ident or (
            get_current_user(), get_current_is_admin(), get_current_role() or "")
        base_ctx = getattr(g, "tool_ctx", None)
        if base_ctx is not None:
            # session 传 None → for_run 保留基础上下文的会话标识
            ctx = base_ctx.for_run(owner, None, is_admin, user_role)
        else:
            ctx = ToolContext(kb=g.kb, security=g.cfg.security, user=owner,
                              is_admin=is_admin, user_role=user_role)
        # 工具裁剪：与主链路同一门控（plugin__/mcp__ 按归属 + 共享 + 角色过滤），
        # 再禁用委派，避免 reference agent 递归派生子 agent。
        ref_specs = [s for s in filter_tool_specs(ToolRegistry.specs(), owner,
                                                  is_admin, user_role)
                     if s["function"]["name"] != DELEGATE_TOOL_NAME]
        allowed_names = {s["function"]["name"] for s in ref_specs}
        sys_prompt = g.cfg.system_prompt
        msgs = [{"role": "system", "content": sys_prompt}] + list(messages)

        resp = await llm.chat(msgs, model=model, tools=ref_specs if ref_specs else None)
        msg = resp["choices"][0]["message"]
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except json.JSONDecodeError:
                    args = {}
                # 纵深防御：模型幻觉出裁剪清单之外的工具名时直接拒绝执行。
                if name not in allowed_names:
                    res = f"[拒绝] 工具 {name} 不在当前用户可用范围内。"
                else:
                    res = await ToolRegistry.execute(name, args, ctx)
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
