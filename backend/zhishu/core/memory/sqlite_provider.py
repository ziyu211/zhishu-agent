"""智枢智能体 —— SQLite 会话记忆（内置 MemoryProvider）。

  保留原 MemoryStore 的全部行为（SQLite FTS5 + 关键词检索），并包成
  MemoryProvider 供 MemoryManager 编排。历史/追加仍由 Agent 直接调用本类，
  以与重构前行为完全一致。
"""
from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from .provider import MemoryProvider


class MemoryStore:
    def __init__(self, path: str = "data/zhishu_memory.db"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT,
                role TEXT,
                content TEXT,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(session, content)"
            )
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def append(self, session: str, role: str, content: str):
        self.conn.execute(
            "INSERT INTO turns (session, role, content) VALUES (?,?,?)",
            (session, role, content),
        )
        try:
            self.conn.execute(
                "INSERT INTO turns_fts (session, content) VALUES (?,?)",
                (session, content),
            )
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def history(self, session: str, limit: int = 20) -> List[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM turns WHERE session=? ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def recall(self, session: str, query: str, limit: int = 5) -> List[str]:
        try:
            rows = self.conn.execute(
                "SELECT content FROM turns_fts WHERE turns_fts MATCH ? AND session=? "
                "ORDER BY rank LIMIT ?",
                (query, session, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            like = f"%{query}%"
            rows = self.conn.execute(
                "SELECT content FROM turns WHERE session=? AND content LIKE ? LIMIT ?",
                (session, like, limit),
            ).fetchall()
        return [r[0] for r in rows]

    def clear(self, session: str = None):
        if session:
            self.conn.execute("DELETE FROM turns WHERE session=?", (session,))
        else:
            self.conn.execute("DELETE FROM turns")
        self.conn.commit()

    def clear_session_prefix(self, prefix: str) -> int:
        """删除 session 以 prefix 开头的全部 turns（含委派子会话 cid::delegate::x）。

        用于级联清理：删除对话/用户时，其服务端记忆（turns）必须一并清除，
        否则已删内容仍可被 session_search 召回、且构成数据残留（合规闭环）。
        prefix 中的 % / _ 通配符已转义，避免用户名含这些字符时误删他人数据。
        """
        esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = esc + "%"
        cur = self.conn.execute(
            "DELETE FROM turns WHERE session LIKE ? ESCAPE '\\'", (pattern,)
        )
        try:
            self.conn.execute(
                "DELETE FROM turns_fts WHERE session LIKE ? ESCAPE '\\'", (pattern,)
            )
        except sqlite3.OperationalError:
            pass
        self.conn.commit()
        return cur.rowcount


class SQLiteMemoryProvider(MemoryProvider):
    """内置会话记忆 Provider：包装 MemoryStore。"""

    def __init__(self, path: str = "data/zhishu_memory.db", store: Optional[MemoryStore] = None):
        self.store = store or MemoryStore(path)

    async def sync_turn(self, owner: Optional[str] = None, role: str = "",
                        content: str = "") -> None:
        session = owner or "default"
        self.store.append(session, role, content)
