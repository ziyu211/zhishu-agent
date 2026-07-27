"""多 Agent 委派工具（仅主管智能体可用；实时流由 Agent.run 拦截转发）。"""
from __future__ import annotations

from ..base import tool, ToolContext


@tool(
    "delegate_to_agent",
    "（仅主管智能体可用）把任务委派给某个子智能体协同处理。"
    "传入子智能体名称与任务，引擎会以其独立人设与工具集运行并返回结果；"
    "你可基于其返回继续综合作答。可选子智能体见管理后台「智能体」模块。",
    {
        "type": "object",
        "properties": {
            "agent_name": {"type": "string", "description": "子智能体名称，如 translator / coder / summarizer"},
            "task": {"type": "string", "description": "交给该子智能体的具体任务描述"},
        },
        "required": ["agent_name", "task"],
    },
    toolset="agent",
)
async def delegate_to_agent(args: dict, ctx) -> str:
    """兜底实现：正常流程由 Agent.run 拦截做实时流式转发。

    此 handler 仅在未走拦截路径时生效，保证工具始终可用。
    """
    from ....context import get_ctx
    from ...agent import Agent
    from ...agents_runtime import get_agent_meta, is_enabled

    g = get_ctx()
    name = args.get("agent_name") or args.get("agent")
    task = (args.get("task") or "").strip()
    if not name or not task:
        return "[委派失败] 缺少 agent_name 或 task 参数"
    meta = get_agent_meta(name)
    if not meta:
        return f"[委派失败] 未找到子智能体：{name}"
    if not is_enabled(name):
        return f"[委派失败] 子智能体已停用：{name}"
    sub_ctx = ToolContext(
        kb=g.kb, security=g.cfg.security,
        user=ctx.user or "anonymous", session=ctx.session,
    )
    sub = Agent(g.cfg, g.llm, g.kb, g.memory, sub_ctx, media=g.media)
    chunks = []
    async for ev in sub.run(task, ctx.session, model=meta.get("model"),
                            owner=ctx.user, agent_name=name):
        if ev.get("type") == "token":
            chunks.append(ev["text"])
    return ("【子智能体 %s 返回】\n" % name) + "".join(chunks)
