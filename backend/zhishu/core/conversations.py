"""智枢智能体 —— 多用户对话存储（SQLite，按 owner 隔离）。

每个对话归属一个用户（owner=登录名）。普通用户只能访问自己的对话；
管理员（role=admin）可通过 scope=all 读取/管理全部对话。
配合 chat.py 的会话归属校验与记忆命名空间隔离，确保「用户 A 无法读取用户 B 的对话」。
"""
from __future__ import annotations

import json
import os
import sqlite3
import secrets
from typing import Optional


class ConversationStore:
    def __init__(self, path: str = "data/zhishu_conversations.db"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                title TEXT DEFAULT '新对话',
                pinned INTEGER DEFAULT 0,
                messages TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.conn.commit()

    # --------------------- 内部工具 ---------------------
    def _row(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["messages"] = json.loads(d.get("messages") or "[]")
        except Exception:
            d["messages"] = []
        d["pinned"] = bool(d.get("pinned"))
        d["message_count"] = len(d["messages"])
        return d

    # --------------------- 变更 ---------------------
    def create(self, owner: str, title: str = "新对话", cid: Optional[str] = None) -> dict:
        cid = cid or ("s_" + secrets.token_hex(8))
        self.conn.execute(
            "INSERT OR IGNORE INTO conversations (id, owner, title, pinned, messages) VALUES (?,?,?,0,'[]')",
            (cid, owner, title or "新对话"),
        )
        self.conn.commit()
        return self.get(cid)

    def list(self, owner: Optional[str] = None, scope: str = "mine") -> list[dict]:
        if scope == "all":
            rows = self.conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM conversations WHERE owner=? ORDER BY updated_at DESC",
                (owner,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, cid: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return self._row(row) if row else None

    def get_for(self, cid: str, user: str, role: str) -> Optional[dict]:
        """返回对话；若不存在返回 None（由调用方决定创建或 404）。
        若对话不属于当前用户且非管理员 → 抛 PermissionError('forbidden')。"""
        conv = self.get(cid)
        if not conv:
            return None
        if conv["owner"] != user and role != "admin":
            raise PermissionError("forbidden")
        return conv

    def update(self, cid: str, user: str, role: str, **fields) -> dict:
        conv = self.get(cid)
        if not conv:
            raise ValueError("not_found")
        if conv["owner"] != user and role != "admin":
            raise PermissionError("forbidden")
        allowed = {"title", "pinned", "messages"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                if k == "messages" and not isinstance(v, str):
                    v = json.dumps(v, ensure_ascii=False)
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return conv
        sets.append("updated_at=CURRENT_TIMESTAMP")
        vals.append(cid)
        self.conn.execute(
            f"UPDATE conversations SET {', '.join(sets)} WHERE id=?", vals
        )
        self.conn.commit()
        return self.get(cid)

    def delete(self, cid: str, user: str, role: str):
        conv = self.get(cid)
        if not conv:
            raise ValueError("not_found")
        if conv["owner"] != user and role != "admin":
            raise PermissionError("forbidden")
        self.conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        self.conn.commit()

    def owner_of(self, cid: str) -> Optional[str]:
        row = self.conn.execute("SELECT owner FROM conversations WHERE id=?", (cid,)).fetchone()
        return row["owner"] if row else None
