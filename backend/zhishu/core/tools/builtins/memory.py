"""长期记忆读写工具（对标 Hermes `tools/memory_tool.py`）。

让智能体在对话中主动沉淀 / 检索可跨会话复用的知识：用户偏好、项目事实、
约定、踩坑记录等。记忆持久化到 ``data_dir`` 下的 ``MEMORY.md`` / ``USER.md`` /
``SOUL.md``，下一轮会自动注入系统提示（见 ``modules/skills.build_agent_context_prompt``）。

与 hermes 一致：内置记忆是纯文本文件，直接拼进系统提示，无向量、无隐藏 schema。
"""
from __future__ import annotations

import os
import re
import threading
from typing import Optional

from ..base import tool

_LOCK = threading.Lock()
_FILES = {
    "memory": "MEMORY.md",
    "user": "USER.md",
    "soul": "SOUL.md",
}
_KEY_LABEL = {"memory": "长期记忆", "user": "用户画像", "soul": "人格设定"}


def _data_dir() -> str:
    from ....context import get_ctx
    return get_ctx().cfg.server.data_dir


def _path(key: str) -> str:
    return os.path.join(_data_dir(), _FILES.get(key, "MEMORY.md"))


def _read(key: str) -> str:
    p = _path(key)
    if not os.path.isfile(p):
        return ""
    try:
        return open(p, encoding="utf-8").read().strip()
    except Exception:
        return ""


def _append(key: str, text: str) -> None:
    p = _path(key)
    with _LOCK:
        head = ""
        if os.path.isfile(p):
            try:
                head = open(p, encoding="utf-8").read().rstrip() + "\n"
            except Exception:
                head = ""
        with open(p, "w", encoding="utf-8") as f:
            f.write(head + text.rstrip() + "\n")


def _replace_section(key: str, text: str) -> None:
    """整体覆盖某个记忆文件（用于 user/soul 画像更新）。"""
    p = _path(key)
    with _LOCK:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text.strip() + "\n")


def _forget(key: str, keyword: str) -> int:
    p = _path(key)
    if not os.path.isfile(p) or not keyword:
        return 0
    with _LOCK:
        lines = open(p, encoding="utf-8").read().splitlines()
        kept = [ln for ln in lines if keyword not in ln]
        removed = len(lines) - len(kept)
        if removed:
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(kept).rstrip() + "\n")
        return removed


@tool(
    "memory",
    "长期记忆读写（对标 Hermes memory_tool）。在对话中主动沉淀 / 检索可跨会话复用的知识："
    "用户偏好、项目事实、约定、踩坑记录等。支持 recall(读取) / save(追加快照) / "
    "update_user(覆盖用户画像) / forget(按关键词删除)。记忆持久化到系统的 MEMORY.md / "
    "USER.md / SOUL.md，下一轮会自动注入系统提示，让智能体「记得」过往。",
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["recall", "save", "update_user", "forget"],
                "description": "操作类型：recall=读取记忆；save=向长期记忆追加一条；"
                               "update_user=覆盖用户画像(USER.md)；forget=按关键词删除记忆条目",
            },
            "key": {
                "type": "string",
                "enum": ["memory", "user", "soul"],
                "description": "目标记忆文件：memory=通用长期记忆(默认) / user=用户画像 / soul=人格设定",
            },
            "content": {
                "type": "string",
                "description": "save / update_user 时使用：要写入的文本内容（update_user 建议为完整画像 Markdown）",
            },
            "keyword": {
                "type": "string",
                "description": "forget 时使用：删除包含该关键词的整行记忆",
            },
            "query": {
                "type": "string",
                "description": "recall 时可选：仅返回包含该关键词的记忆行（留空返回全部）",
            },
        },
        "required": ["action"],
    },
    toolset="memory",
)
async def memory(args: dict, ctx) -> str:
    action = (args.get("action") or "recall").lower()
    key = (args.get("key") or "memory").lower()
    if key not in _FILES:
        key = "memory"

    # ---- recall ----
    if action == "recall":
        q = (args.get("query") or "").strip()
        parts: list[str] = []
        targets = [key] if key != "memory" else list(_FILES.keys())
        for k in targets:
            txt = _read(k)
            if not txt:
                continue
            if q:
                matched = [ln for ln in txt.splitlines() if q in ln]
                if not matched:
                    continue
                txt = "\n".join(matched)
            parts.append(f"【{_KEY_LABEL[k]}】\n{txt}")
        if not parts:
            return "[memory] 暂无记忆" + (f"（关键词：{q}）" if q else "")
        return "[memory] 当前记忆：\n\n" + "\n\n".join(parts)

    # ---- save（追加一条到 MEMORY.md）----
    if action == "save":
        content = (args.get("content") or "").strip()
        if not content:
            return "[memory] save 需要提供 content"
        # 规整为单行条目
        content = re.sub(r"\s+", " ", content)
        k = "user" if key == "user" else "memory"
        _append(k, f"- {content}")
        return f"[memory] 已保存到{_KEY_LABEL[k]}：{content[:120]}"

    # ---- update_user（整体覆盖 USER.md）----
    if action == "update_user":
        content = (args.get("content") or "").strip()
        if not content:
            return "[memory] update_user 需要提供 content"
        _replace_section("user", content)
        return f"[memory] 已更新用户画像(USER.md)，共 {len(content.splitlines())} 行"

    # ---- forget ----
    if action == "forget":
        kw = (args.get("keyword") or "").strip()
        if not kw:
            return "[memory] forget 需要提供 keyword"
        removed = _forget(key, kw)
        return f"[memory] 从{_KEY_LABEL[key]}删除 {removed} 条包含「{kw}」的记忆"

    return f"[memory] 未知 action：{action}"
