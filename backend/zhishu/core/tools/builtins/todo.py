"""任务清单工具（对标 Hermes todo_tool：单工具读写、进程级每会话一份）。

设计（与 Hermes 一致）：
  * 单一 `todo` 工具：传 todos 参数即写入，省略即读取；每次调用都返回完整清单。
  * 清单存放在进程级 dict（按会话隔离），复杂任务拆解、跨长对话保持专注。
  * merge=true 时按 id 增量更新（只改传入的项），否则整表替换。

护栏：单项内容截断、总条数上限，防止清单本身膨胀占用上下文。
"""
from __future__ import annotations

from ..base import tool, ToolContext

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_STATUS_MARK = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "cancelled": "[-]"}
MAX_ITEMS = 100
MAX_CONTENT = 500

# 进程级存储：session -> 有序清单（列表位置即优先级）
_STORES: dict[str, list[dict]] = {}


def _validate(item: dict) -> dict | None:
    iid = str(item.get("id", "")).strip()
    content = str(item.get("content", "")).strip()[:MAX_CONTENT]
    status = str(item.get("status", "pending")).strip().lower()
    if not iid or not content:
        return None
    if status not in VALID_STATUSES:
        status = "pending"
    return {"id": iid, "content": content, "status": status}


def _render(items: list[dict]) -> str:
    if not items:
        return "[todo] 清单为空。传入 todos 参数（[{id, content, status}]）创建任务。"
    done = sum(1 for t in items if t["status"] == "completed")
    lines = [f"[todo] 共 {len(items)} 项，已完成 {done}："]
    for t in items:
        lines.append(f"{_STATUS_MARK[t['status']]} ({t['id']}) {t['content']}")
    return "\n".join(lines)


@tool(
    "todo",
    "任务清单（规划与进度跟踪）：把复杂任务拆解为待办项并逐项推进，保持长对话不跑偏。"
    "传 todos 参数（数组，每项 {id, content, status}）即写入并返回最新清单；"
    "省略 todos 即只读当前清单。status 取值 pending/in_progress/completed/cancelled。"
    "merge=true 时按 id 增量更新（只改传入项），默认整表替换。"
    "使用习惯：开始复杂任务先写入拆解清单；每完成一项立即更新状态。",
    {"type": "object", "properties": {
        "todos": {"type": "array", "description": "待办项数组 [{id, content, status}]；省略则只读",
                  "items": {"type": "object", "properties": {
                      "id": {"type": "string"},
                      "content": {"type": "string"},
                      "status": {"type": "string"},
                  }}},
        "merge": {"type": "boolean", "description": "true=按 id 增量更新，false(默认)=整表替换"},
    }},
    toolset="todo",
)
async def todo(args: dict, ctx: ToolContext) -> str:
    session = getattr(ctx, "session", "default") or "default"
    items = _STORES.setdefault(session, [])
    todos = args.get("todos")
    if todos is None:
        return _render(items)
    if not isinstance(todos, list):
        return "[todo] todos 参数须为数组 [{id, content, status}]"
    incoming = [v for v in (_validate(t) for t in todos if isinstance(t, dict)) if v]
    if args.get("merge"):
        by_id = {t["id"]: t for t in items}
        for t in incoming:
            if t["id"] in by_id:
                by_id[t["id"]].update(t)
            else:
                items.append(t)
    else:
        items[:] = incoming
    del items[MAX_ITEMS:]
    return _render(items)
