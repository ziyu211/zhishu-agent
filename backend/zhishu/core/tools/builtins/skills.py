"""技能读取工具（渐进披露）：模型按需读取 SKILL.md 全文。"""
from __future__ import annotations

import json
import os
import re

from ..base import tool


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "", (name or "").strip())[:64]


@tool(
    "read_skill",
    "读取某个技能(SKILL.md)的完整指令内容。当需要在回答中应用某技能的详细方法时使用；"
    "系统提示中仅列出技能名称与简介，详情需本工具按需获取。",
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "技能名称（与技能目录同名）"}},
     "required": ["name"]},
    toolset="skills",
)
async def read_skill(args: dict, ctx) -> str:
    name = _sanitize(args.get("name"))
    if not name:
        return "[read_skill] 技能名无效"
    from ....context import get_ctx
    base = get_ctx().cfg.server.data_dir
    d = os.path.join(base, "skills", name)
    if not os.path.isdir(d):
        return f"[read_skill] 未找到技能：{name}"
    # 多用户隔离：他人私有技能视同不存在（防枚举探测 + 正文泄露）。
    # 身份优先取 ctx（本次运行专用副本），缺失时回退 contextvars（task-local）。
    from ...modules.runtime import module_owner, module_shared, module_share_with, can_view
    from ..base import get_current_user, get_current_is_admin, get_current_role
    _user = getattr(ctx, "user", None) or get_current_user()
    _is_admin = bool(getattr(ctx, "is_admin", False)) or get_current_is_admin()
    _role = getattr(ctx, "user_role", None) or get_current_role()
    if not can_view(module_owner("skills", name), _user, _is_admin, module_shared("skills", name),
                    module_share_with("skills", name), _role):
        return f"[read_skill] 未找到技能：{name}"
    md = os.path.join(d, "SKILL.md")
    if os.path.isfile(md):
        try:
            return open(md, encoding="utf-8").read()[:8000]
        except Exception as e:
            return f"[read_skill] 读取失败：{e}"
    meta = os.path.join(d, "module.json")
    if os.path.isfile(meta):
        try:
            return (json.load(open(meta, encoding="utf-8")).get("content") or "（无内容）")[:8000]
        except Exception as e:
            return f"[read_skill] 读取失败：{e}"
    return f"[read_skill] 技能 {name} 无内容"
