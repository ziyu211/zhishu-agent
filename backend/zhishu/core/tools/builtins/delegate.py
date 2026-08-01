"""多 Agent 委派工具（仅主管智能体可用；实时流由 Agent.run 拦截转发）。"""
from __future__ import annotations

from ..base import tool, ToolContext, get_current_role


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
    from ...agents_runtime import agent_owner, get_agent_meta, is_enabled
    from ...modules.runtime import can_view

    g = get_ctx()
    name = args.get("agent_name") or args.get("agent")
    task = (args.get("task") or "").strip()
    if not name or not task:
        return "[委派失败] 缺少 agent_name 或 task 参数"
    is_admin = bool(getattr(ctx, "is_admin", False))
    user_role = getattr(ctx, "user_role", None) or get_current_role()
    meta = get_agent_meta(name)
    if not meta:
        return f"[委派失败] 未找到子智能体：{name}"
    # 多用户隔离：他人私有/非角色命中子智能体视同不存在（防枚举探测）
    if not can_view(meta.get("owner") or None, ctx.user, is_admin, bool(meta.get("shared")),
                    meta.get("share_with") or None, user_role):
        return f"[委派失败] 未找到子智能体：{name}"
    if not is_enabled(name):
        return f"[委派失败] 子智能体已停用：{name}"
    # 委派审计（动作级）：记录谁把任务委派给了哪个子智能体
    try:
        g.audit.log(ctx.user or "anonymous", "delegate",
                    f"target={name} task={(task or '')[:160]}")
    except Exception:
        pass
    # 子智能体使用独立 scratch 会话，避免污染主会话历史；标注 agent_name 供工具审计归属
    scratch_session = f"{ctx.session}::delegate::{name}"
    sub_ctx = ToolContext(
        kb=g.kb, security=g.cfg.security,
        user=ctx.user or "anonymous", session=scratch_session, is_admin=is_admin,
        agent_name=name,
    )
    sub = Agent(g.cfg, g.llm, g.kb, g.memory, sub_ctx, media=g.media,
                memory_manager=None)
    chunks = []
    async for ev in sub.run(task, scratch_session, model=meta.get("model"),
                            owner=ctx.user, agent_name=name, is_admin=is_admin):
        if ev.get("type") == "token":
            chunks.append(ev["text"])
    return ("【子智能体 %s 返回】\n" % name) + "".join(chunks)
