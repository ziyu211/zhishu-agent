"""动态组建多智能体团队工具（主管在对话中按需创建协调者与执行成员）。

让用户用自然语言描述团队结构（如「创建股票分析团队：Orchestrator=投资总监、
Research=行业研究员…」），主管即可调用本工具一次性落盘一个「1 个协调者 + N 个执行成员」
的团队，系统自动为每个成员撰写系统提示词，并写入协调者的 ``sub_agents`` 关联，
使既有的编排引擎（覆盖度闸门 / 即时补齐 / 超时 fanout）精确生效。
"""
from __future__ import annotations

import os
from typing import Optional

from ..base import tool, ToolContext
from ...agents_runtime import write_agent_meta, sanitize_name, agent_dir

# 子智能体默认工具集（执行类，不含 delegate_to_agent，避免成员越级委派造成递归）
DEFAULT_MEMBER_TOOLS = [
    "web_search", "safe_web_fetch", "knowledge_search", "knowledge_list",
    "knowledge_read", "file_read", "file_list", "code_exec", "read_skill",
    "session_search", "todo", "memory",
]


def _gen_coordinator_prompt(team_name: str, title: str, responsibility: str,
                            members: list[dict]) -> str:
    member_lines = "\n".join(
        f"- {m['name']}（{m['title']}）：{m['responsibility']}" for m in members
    )
    return (
        f"你是「{team_name}」的{title}，也是该团队的协调者与总调度。\n"
        f"你的核心职责：{responsibility}\n\n"
        f"你手下可调度的子智能体成员：\n{member_lines}\n\n"
        "工作准则：\n"
        "1. 收到任务后，先用 delegate_to_agent 把任务拆分并分派给最相关的成员，"
        "禁止自己替代成员执行其专业工作。\n"
        "2. 各成员返回结果后，你负责汇总、交叉验证、整合，给出面向用户的最终结论。\n"
        "3. 若某成员缺失能力，可继续委派给其他成员或请求补充信息。"
    )


def _gen_member_prompt(team_name: str, title: str, responsibility: str) -> str:
    return (
        f"你是「{team_name}」的{title}，是该团队中负责「{title}」这一专业角色的执行智能体。\n"
        f"你的核心职责：{responsibility}\n\n"
        "工作准则：\n"
        "1. 收到协调者分派的任务后，运用你的专业能力独立完成，返回结构化、可引用的结果。\n"
        "2. 若任务超出你的职责范围或缺少必要信息，明确说明限制，不要编造。\n"
        "3. 结果应便于协调者直接汇总整合。"
    )


def _resolve_tools(spec) -> object:
    """member_tools 参数：'default'（推荐）/ 'all' / 逗号分隔工具名 / 列表。"""
    if spec is None:
        return DEFAULT_MEMBER_TOOLS
    if isinstance(spec, list):
        return spec
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s in ("", "default"):
            return DEFAULT_MEMBER_TOOLS
        if s == "all":
            return "all"
        return [t.strip() for t in spec.split(",") if t.strip()]
    return DEFAULT_MEMBER_TOOLS


@tool(
    "create_team",
    "【主管建团工具】当用户要求组建/创建多智能体团队、或任务明显需要多角色协作完成时，"
    "调用本工具一次性创建「1 个协调者 + N 个执行成员」的团队，系统会自动为每个成员撰写系统提示词。"
    "创建后请用 delegate_to_agent 把用户任务整体委派给协调者，由其内部分派给各成员。\n"
    "参数说明：coordinator 为总调度（如投资总监），members 为执行成员列表；"
    "name 用简短英文（如 Orchestrator/Research），title 与 responsibility 用中文描述其角色与职责；"
    "system_prompt 可留空由系统自动生成。",
    {
        "type": "object",
        "properties": {
            "team_name": {"type": "string", "description": "团队名称，如「股票分析团队」"},
            "coordinator": {
                "type": "object",
                "description": "协调者（总调度），负责把任务分派给成员",
                "properties": {
                    "name": {"type": "string", "description": "协调者 agent 名（英文，如 Orchestrator）"},
                    "title": {"type": "string", "description": "职位，如 投资总监"},
                    "responsibility": {"type": "string", "description": "职责描述"},
                    "system_prompt": {"type": "string", "description": "可选自定义系统提示词；留空由系统生成"},
                },
                "required": ["name", "title", "responsibility"],
            },
            "members": {
                "type": "array",
                "description": "执行成员列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "成员 agent 名（英文，如 Research）"},
                        "title": {"type": "string", "description": "职位，如 行业研究员"},
                        "responsibility": {"type": "string", "description": "职责描述"},
                        "system_prompt": {"type": "string", "description": "可选自定义系统提示词；留空由系统生成"},
                    },
                    "required": ["name", "title", "responsibility"],
                },
            },
            "model": {"type": "string", "description": "可选：团队统一模型；留空继承默认"},
            "member_tools": {"type": "string",
                             "description": "可选：成员工具集，'default'(推荐)/'all'/逗号分隔工具名；默认 default"},
        },
        "required": ["coordinator", "members"],
    },
    toolset="agent",
)
async def create_team(args: dict, ctx: ToolContext) -> str:
    coord = args.get("coordinator") or {}
    members = args.get("members") or []
    if not isinstance(coord, dict) or not coord.get("name"):
        return "[创建团队失败] 缺少 coordinator.name"
    if not isinstance(members, list) or not members:
        return "[创建团队失败] members 必须为非空列表"

    team_name = (args.get("team_name") or "智能体团队").strip() or "智能体团队"
    model = args.get("model") or None
    member_tools = _resolve_tools(args.get("member_tools"))
    owner = getattr(ctx, "user", None) or None

    def _parse_role(r: dict):
        name = sanitize_name(str(r.get("name", "")))
        title = (r.get("title") or "").strip()
        resp = (r.get("responsibility") or "").strip()
        custom = (r.get("system_prompt") or "").strip()
        return name, title, resp, custom

    c_name, c_title, c_resp, c_custom = _parse_role(coord)
    if not c_name:
        return "[创建团队失败] 协调者名称非法（仅允许字母数字 _.-）"

    member_specs: list[dict] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        n, t, r, c = _parse_role(m)
        if not n:
            return f"[创建团队失败] 成员名称非法：{m.get('name')}"
        member_specs.append({"name": n, "title": t, "responsibility": r, "custom": c})

    # 重名检查（避免覆盖已有 agent）
    existing: list[str] = []
    for spec in [{"name": c_name}] + [{"name": s["name"]} for s in member_specs]:
        if os.path.isdir(agent_dir(spec["name"])):
            existing.append(spec["name"])
    if existing:
        return ("[创建团队失败] 以下智能体已存在，无法重复创建（如需重建请先删除）："
                + ", ".join(existing))

    # 写协调者：tools 必须显式含 delegate_to_agent，否则不被识别为协调类；
    # sub_agents 写入成员清单，使 _expected_sub_agents 走显式分支。
    coord_meta = {
        "name": c_name,
        "description": (f"{c_title}：{c_resp}" if c_title else c_resp),
        "version": "1.0.0",
        "enabled": True,
        "system_prompt": c_custom or _gen_coordinator_prompt(
            team_name, c_title, c_resp,
            [{"name": s["name"], "title": s["title"], "responsibility": s["responsibility"]}
             for s in member_specs],
        ),
        "model": model,
        "tools": ["delegate_to_agent"],
        "sub_agents": [s["name"] for s in member_specs],
        "owner": owner,
        "shared": False,
        "share_with": [],
    }
    write_agent_meta(c_name, coord_meta)

    # 写成员
    created: list[str] = []
    for s in member_specs:
        meta = {
            "name": s["name"],
            "description": (f"{s['title']}：{s['responsibility']}" if s["title"] else s["responsibility"]),
            "version": "1.0.0",
            "enabled": True,
            "system_prompt": s["custom"] or _gen_member_prompt(
                team_name, s["title"], s["responsibility"]),
            "model": model,
            "tools": list(member_tools) if isinstance(member_tools, list) else member_tools,
            "owner": owner,
            "shared": False,
            "share_with": [],
        }
        write_agent_meta(s["name"], meta)
        created.append(s["name"])

    return (
        f"[团队创建成功] 团队「{team_name}」已组建：\n"
        f"- 协调者：{c_name}（{c_title}）\n"
        f"- 成员：{', '.join(created)}\n"
        f"请立即将用户的原始任务整体委派给协调者 {c_name}，由其内部分派给各成员并汇总。"
    )
