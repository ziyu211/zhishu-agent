"""技能读取工具（渐进披露）：模型按需读取 SKILL.md 全文。"""
from __future__ import annotations

import json
import os
import re

from ..base import tool, get_current_user


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


@tool(
    "create_skill",
    "将一份技能（SKILL.md 正文）持久化保存到「功能模块技能」列表（磁盘技能库）。"
    "保存后：① 重启智枢依然保留；② 可在前端 SkillsView 查看 / 开关 / 导出；"
    "③ 后续所有会话都能经 read_skill(name=...) 读取其完整指令，并（默认 enabled 时）注入系统提示被模型直接复用。"
    "⚠️ 与 create_tool 的区别：create_tool 注册的是『当前会话内临时』的 dyn_ 动态工具，"
    "进程重启即清空、且**不会**出现在功能模块技能列表；当用户明确要求『保存 / 创建技能』并希望长期留存、在技能列表中可见时，"
    "必须用本工具，而非 create_tool。"
    "必填：name（技能目录名，字母/数字/下划线/点/减号，2-48 字符）、content（技能正文 Markdown，"
    "建议含 ## 标题、用途 description、## 步骤、## 示例——这是注入给模型的核心指令）。"
    "可选：description（一句话简介，缺省取正文首行）、shared（是否共享给他人，默认 False=本人私有）、version。",
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "技能名（目录名，2-48 字符，仅字母/数字/下划线/点/减号）"},
        "content": {"type": "string", "description": "技能正文 Markdown（建议含 ## 标题、用途、## 步骤、## 示例；作为注入指令）"},
        "description": {"type": "string", "description": "可选一句话简介，缺省取正文首行"},
        "shared": {"type": "string", "description": "可选：是否对他人可见可用，'true'/'false'，默认 false（当前用户私有）"},
        "version": {"type": "string", "description": "可选版本号，默认 1.0.0"},
     }, "required": ["name", "content"]},
    toolset="skills",
)
async def create_skill(args: dict, ctx) -> str:
    name = _sanitize(args.get("name"))
    if not name or len(name) < 2:
        return "[create_skill] 技能名非法：需为字母/数字/下划线/点/减号，2-48 字符"
    content = (args.get("content") or "").strip()
    if not content:
        return "[create_skill] 缺少 content（技能正文 Markdown）"

    from ....context import get_ctx
    base = get_ctx().cfg.server.data_dir
    d = os.path.join(base, "skills", name)
    if os.path.isdir(d):
        return (f"[create_skill] 技能已存在：{name}（如需更新，请先删除该技能再重建，"
                f"或后续调用更新接口覆盖）")
    os.makedirs(d, exist_ok=True)

    owner = getattr(ctx, "user", None) or get_current_user() or None
    shared = str(args.get("shared", "false")).strip().lower() in ("1", "true", "yes", "y")
    desc = (args.get("description") or "").strip() or content.split("\n", 1)[0].lstrip("#").strip()[:200]
    meta = {
        "name": name,
        "description": desc,
        "version": args.get("version") or "1.0.0",
        "content": content,
        "enabled": True,
        "shared": shared,
        "share_with": [],
    }
    # 多用户隔离：私有技能归属触发保存的用户；匿名/后台任务不写 owner（系统级共享）
    if owner and owner not in ("anonymous", "system"):
        meta["owner"] = owner
    try:
        with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"[create_skill] 写入失败：{e}"

    vis = "（已共享，对他人可见可用）" if shared else "（私有，仅本人可见）"
    return (f"[create_skill] 已持久化技能 '{name}' 到「功能模块技能」（磁盘保存，重启后仍在）。{vis}\n"
            f"可在 SkillsView 查看 / 开关 / 导出；后续会话用 read_skill(name='{name}') 读取其完整指令。")
