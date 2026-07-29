"""智枢智能体 —— 多 Agent 协作运行时（主管-成员模式）。

把"子智能体（Sub-Agent）"作为一等公民的管理模块，与技能/插件/MCP 同一范式：
  data/agents/<name>/agent.json
    {
      "name": "translator",
      "description": "中英互译专家",
      "version": "1.0.0",
      "enabled": true,
      "system_prompt": "你是一位专业的翻译官……",
      "model": null,            // 可选：覆盖默认模型，如 "qwen"；null=用请求模型/默认
      "tools": "all",          // "all" | "none" | ["builtin","plugin__x","mcp__s__t",...]
      "max_steps": 6            // 可选：覆盖单轮最大推理步数
    }

主管（主智能体，agent_name=None）通过内置工具 delegate_to_agent 把任务委派给子智能体；
子智能体拥有独立人设(system_prompt)、可选模型/步数、按 tools 字段裁剪后的工具集。
启停状态存于 data/agents_state.json 的 agents_disabled 列表。
"""
from __future__ import annotations

import json
import os
import re
import shutil

from .tools import ToolRegistry
from .config import ZhishuConfig


AGENT_SUB = "agents"
AGENT_META = "agent.json"
DELEGATE_TOOL_NAME = "delegate_to_agent"


# ---------------------------------------------------------------------------
# 状态读写（agents_state.json）
# ---------------------------------------------------------------------------
def _state_path() -> str:
    from ..context import get_ctx
    data_dir = get_ctx().cfg.server.data_dir
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "agents_state.json")


def load_state() -> dict:
    p = _state_path()
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"agents_disabled": []}


def save_state(state: dict) -> None:
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def disabled_set() -> set:
    return set(load_state().get("agents_disabled", []))


def is_enabled(name: str) -> bool:
    return name not in disabled_set()


def set_enabled(name: str, enabled: bool) -> None:
    state = load_state()
    dis = set(state.get("agents_disabled", []))
    if enabled:
        dis.discard(name)
    else:
        dis.add(name)
    state["agents_disabled"] = sorted(dis)
    save_state(state)


# ---------------------------------------------------------------------------
# 子智能体目录 / 元信息读写
# ---------------------------------------------------------------------------
def agent_dir(name: str) -> str:
    from ..context import get_ctx
    return os.path.join(get_ctx().cfg.server.data_dir, AGENT_SUB, name)


def sanitize_name(name: str) -> str:
    """子智能体名只允许 [A-Za-z0-9_.-]，避免路径穿越。"""
    s = re.sub(r"[^A-Za-z0-9_.\-]", "", name.strip())
    return s[:64]


def read_agent_meta(name: str) -> dict:
    d = agent_dir(name)
    fp = os.path.join(d, AGENT_META)
    if os.path.isfile(fp):
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            return {}
    # 兼容 README 兜底描述（极少用）
    readme = os.path.join(d, "README.md")
    if os.path.isfile(readme):
        try:
            return {"name": name, "description": open(readme, encoding="utf-8").read(400).strip()}
        except Exception:
            pass
    return {}


def write_agent_meta(name: str, meta: dict, create: bool = True) -> dict:
    d = agent_dir(name)
    if create:
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, AGENT_META), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def delete_agent(name: str) -> None:
    d = agent_dir(name)
    if os.path.isdir(d):
        shutil.rmtree(d)


def agent_owner(name: str) -> "str | None":
    """返回子智能体归属用户名；None 表示系统级共享（全员可用，仅 admin 可管理）。"""
    owner = (read_agent_meta(name) or {}).get("owner")
    return str(owner) if owner else None


def list_agents(username: "str | None" = None, is_admin: bool = False) -> list[dict]:
    """列出子智能体（含启用状态与工具数）。

    多用户隔离：默认（username=None 且非 admin）视为匿名，仅见共享智能体；
    普通用户见「共享 + 本人」；admin 见全部。"""
    from ..context import get_ctx
    from .modules.runtime import can_view
    base = os.path.join(get_ctx().cfg.server.data_dir, AGENT_SUB)
    out = []
    if not os.path.isdir(base):
        return out
    disabled = disabled_set()
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        meta = read_agent_meta(name)
        if not meta:
            continue
        owner_val = meta.get("owner") or None
        if not can_view(owner_val, username, is_admin):
            continue
        meta = dict(meta)
        meta["name"] = name
        meta["owner"] = owner_val
        meta["enabled"] = name not in disabled
        try:
            meta["tool_count"] = len(resolve_tools(
                meta.get("tools", "all"), username=username, is_admin=is_admin))
        except Exception:
            meta["tool_count"] = 0
        out.append(meta)
    return out


# ---------------------------------------------------------------------------
# 工具裁剪：按 agent.tools 字段过滤全局工具清单
# ---------------------------------------------------------------------------
def _match(name: str, entry: str) -> bool:
    if entry == name:
        return True
    if entry == "builtin" and not (name.startswith("plugin__") or name.startswith("mcp__")):
        return True
    if entry == "plugin" and name.startswith("plugin__"):
        return True
    if entry == "mcp" and name.startswith("mcp__"):
        return True
    # 前缀匹配：如 "plugin__demo-plugin" 命中 "plugin__demo-plugin__hello"
    return name.startswith(entry + "__") or name == entry


def resolve_tools(field, username: "str | None" = None, is_admin: bool = False) -> list[dict]:
    """返回该子智能体可见的工具声明（OpenAI function-calling 格式）。

    field:
      "all"  / None        -> 全部已注册工具
      "none"               -> 空
      [..]                 -> 按条目过滤（条目可为 builtin/plugin/mcp/具体名/前缀）

    多用户隔离：plugin__/mcp__ 工具先按归属裁剪（共享 + 本人；admin 全量），
    防止子智能体 tools 字段写入他人私有模块即可越权调用。
    """
    from .modules.runtime import filter_tool_specs
    all_specs = filter_tool_specs(ToolRegistry.specs(), username, is_admin)
    if field is None or field == "all":
        return all_specs
    if field == "none":
        return []
    if isinstance(field, str):
        field = [field]
    if not isinstance(field, list):
        return all_specs
    out = []
    for spec in all_specs:
        nm = spec["function"]["name"]
        if any(_match(nm, e) for e in field):
            out.append(spec)
    return out


def build_agent_system_prompt(name: str) -> str:
    """返回子智能体的系统提示（即其 person 设定）。"""
    meta = read_agent_meta(name)
    return (meta.get("system_prompt") or "").strip()


def get_agent_meta(name: str) -> dict:
    """供委派工具/ Agent 运行取用：返回带启用检查的元信息。"""
    if not name:
        return {}
    meta = read_agent_meta(name)
    if not meta:
        return {}
    meta = dict(meta)
    meta["name"] = name
    meta["enabled"] = is_enabled(name)
    return meta
