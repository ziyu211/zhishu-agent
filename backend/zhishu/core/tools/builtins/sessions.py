"""跨会话回忆工具（对标 Hermes session_search_tool 的单工具三模式设计）。

数据源：内置 SQLite 会话记忆（MemoryStore，turns 表 + turns_fts FTS5 索引），
零 LLM 成本，所有模式都返回数据库中的真实消息。

三种调用模式（按参数自动推断，无显式 mode 参数）：
  1. 检索（DISCOVERY） —— 传 query：跨会话全文检索，按会话去重，
     每个命中会话返回匹配片段与最近时间。
  2. 翻阅（SCROLL）     —— 传 session_id：返回该会话最近 window 条消息原文。
  3. 浏览（BROWSE）     —— 不传参数：按时间倒序列出最近会话（预览+时间）。
"""
from __future__ import annotations

import sqlite3

from ..base import tool, ToolContext

_MAX_SNIPPET = 240
_MAX_SESSIONS = 8
_MAX_WINDOW = 40


def _conn():
    from ....context import get_ctx
    return get_ctx().memory.conn


def _preview(text: str, n: int = _MAX_SNIPPET) -> str:
    t = (text or "").replace("\n", " ").strip()
    return t[:n] + ("…" if len(t) > n else "")


def _discover(conn, query: str, limit: int, owner_prefix: str) -> str:
    """跨会话 FTS 检索，按会话去重。FTS5(trigram) 主召回 + LIKE 兜底合并，最大化召回。

    安全：所有查询强制按 ``owner_prefix``（``{owner}:%``）过滤，
    仅检索当前用户自己的会话，防止跨用户历史泄露。
    """
    fts_rows: list[tuple] = []
    try:
        fts_rows = conn.execute(
            "SELECT session, content FROM turns_fts WHERE turns_fts MATCH ? "
            "AND session LIKE ? ORDER BY rank LIMIT 100",
            (query, owner_prefix)).fetchall()
    except sqlite3.OperationalError:
        fts_rows = []
    like_rows: list[tuple] = []
    # <3 字符的短查询 trigram 不索引，或 FTS 未命中时，用 LIKE 兜底（中文短词/专名也能召回）
    if len(query) < 3 or not fts_rows:
        like_rows = conn.execute(
            "SELECT session, content FROM turns WHERE content LIKE ? "
            "AND session LIKE ? ORDER BY id DESC LIMIT 100",
            (f"%{query}%", owner_prefix)).fetchall()
    rows = list(fts_rows) + list(like_rows)
    if not rows:
        return f"[session_search] 未在历史会话中检索到「{query}」相关内容。"
    # 按会话去重（保留首个命中片段）
    seen: dict[str, str] = {}
    for sess, content in rows:
        if sess not in seen:
            seen[sess] = content
        if len(seen) >= limit:
            break
    lines = [f"[session_search] 检索「{query}」命中 {len(seen)} 个会话："]
    for sess, content in seen.items():
        ts_row = conn.execute(
            "SELECT MAX(ts) FROM turns WHERE session=?", (sess,)).fetchone()
        ts = (ts_row[0] or "") if ts_row else ""
        lines.append(f"- 会话 {sess}（最近活动 {ts}）")
        lines.append(f"  片段：{_preview(content)}")
    lines.append("提示：传 session_id 参数可翻阅该会话的完整消息。")
    return "\n".join(lines)


def _scroll(conn, session_id: str, window: int, owner: str) -> str:
    # 安全：session 键为 "owner:session" 格式。无论模型传入什么 session_id，
    # 都强制归一化到当前用户命名空间，杜绝翻阅他人会话。
    sid = session_id if session_id.startswith(f"{owner}:") else f"{owner}:{session_id}"
    rows = conn.execute(
        "SELECT role, content, ts FROM turns WHERE session=? "
        "ORDER BY id DESC LIMIT ?", (sid, window)).fetchall()
    if not rows:
        return f"[session_search] 会话 {session_id} 不存在或无消息。"
    lines = [f"[session_search] 会话 {session_id} 最近 {len(rows)} 条消息（旧→新）："]
    for role, content, ts in reversed(rows):
        lines.append(f"[{ts}] {role}: {_preview(content, 400)}")
    return "\n".join(lines)


def _browse(conn, limit: int, owner_prefix: str) -> str:
    rows = conn.execute(
        "SELECT session, MAX(ts) AS last_ts, COUNT(*) AS n FROM turns "
        "WHERE session LIKE ? "
        "GROUP BY session ORDER BY last_ts DESC LIMIT ?",
        (owner_prefix, limit)).fetchall()
    if not rows:
        return "[session_search] 尚无历史会话。"
    lines = [f"[session_search] 最近 {len(rows)} 个会话："]
    for sess, last_ts, n in rows:
        first = conn.execute(
            "SELECT content FROM turns WHERE session=? AND role='user' "
            "ORDER BY id LIMIT 1", (sess,)).fetchone()
        preview = _preview(first[0] if first else "", 120)
        lines.append(f"- {sess}（{n} 条，最近 {last_ts}）：{preview}")
    lines.append("提示：传 query 可跨会话检索，传 session_id 可翻阅具体会话。")
    return "\n".join(lines)


@tool(
    "session_search",
    "跨会话长期回忆：检索/翻阅/浏览用户的历史对话记录（本地 SQLite，零依赖）。"
    "当用户提到「之前聊过 / 上次说的 / 帮我回忆」等跨会话内容时使用。"
    "三种模式按参数自动推断：传 query=跨会话全文检索；传 session_id=翻阅该会话消息；"
    "都不传=按时间浏览最近会话列表。",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "检索关键词（跨所有会话全文检索）"},
        "session_id": {"type": "string", "description": "会话 ID（翻阅该会话最近消息）"},
        "limit": {"type": "integer", "description": "返回会话数上限，默认 8"},
        "window": {"type": "integer", "description": "翻阅模式返回消息条数，默认 20"},
    }},
    toolset="sessions",
)
async def session_search(args: dict, ctx: ToolContext) -> str:
    try:
        conn = _conn()
    except Exception as e:
        return f"[session_search] 会话记忆不可用：{e}"
    # 安全：以当前用户为命名空间（turns.session 格式为 "owner:session"），
    # 所有模式一律仅访问本人会话，防止跨用户历史泄露。
    owner = (getattr(ctx, "user", None) or "").strip()
    if not owner:
        return "[session_search] 无法确定当前用户身份，已拒绝访问历史会话。"
    owner_prefix = f"{owner}:%"
    limit = max(1, min(int(args.get("limit") or _MAX_SESSIONS), 20))
    window = max(1, min(int(args.get("window") or 20), _MAX_WINDOW))
    query = (args.get("query") or "").strip()
    session_id = (args.get("session_id") or "").strip()
    try:
        if session_id:
            return _scroll(conn, session_id, window, owner)
        if query:
            return _discover(conn, query, limit, owner_prefix)
        return _browse(conn, limit, owner_prefix)
    except Exception as e:  # noqa: BLE001
        return f"[session_search 失败] {e}"
