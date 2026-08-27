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
        # 优化（全量优化）：将默认 unicode61 分词器升级为 trigram，
        # 以支持中文子串检索（unicode61 会把整段无空格中文当成一个 token，
        # 致使「北京」搜不到「北京市朝阳区」）。trigram 生成 3-gram，对中/英子串
        # 均友好；<3 字符的短查询由 recall/_discover 的 LIKE 兜底覆盖。
        # 迁移幂等：已是 trigram 则跳过；否则 DROP+重建并从 turns 回填。
        self._migrate_fts_tokenizer()
        self.conn.commit()

    def _migrate_fts_tokenizer(self):
        """将 turns_fts 的分词器升级为 trigram（支持中文子串检索）。幂等且安全。"""
        try:
            row = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='turns_fts'"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and row[0] and "trigram" in row[0].lower():
            return  # 已是 trigram，无需迁移
        try:
            self.conn.execute("DROP TABLE IF EXISTS turns_fts")
            self.conn.execute(
                "CREATE VIRTUAL TABLE turns_fts USING fts5("
                "session, content, tokenize='trigram')"
            )
            rows = self.conn.execute("SELECT session, content FROM turns").fetchall()
            if rows:
                self.conn.executemany(
                    "INSERT INTO turns_fts (session, content) VALUES (?,?)", rows
                )
        except sqlite3.OperationalError:
            pass

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
        """检索会话流水（消息级）。

        双路召回、合并去重：
          - FTS5(trigram)：负责中/英子串快速检索（≥3 字符；中文整段也可命中）；
          - LIKE 兜底：覆盖 <3 字符的短查询（trigram 不索引）及 FTS 偶发未命中，
            保证中文短词/专有名词（如「报告」「API」）也能召回。
        """
        fts_rows: list = []
        try:
            fts_rows = self.conn.execute(
                "SELECT content FROM turns_fts WHERE turns_fts MATCH ? AND session=? "
                "ORDER BY rank LIMIT ?",
                (query, session, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            fts_rows = []
        like_rows: list = []
        if len(query) < 3 or not fts_rows:
            try:
                like_rows = self.conn.execute(
                    "SELECT content FROM turns WHERE session=? AND content LIKE ? LIMIT ?",
                    (session, f"%{query}%", limit),
                ).fetchall()
            except sqlite3.OperationalError:
                like_rows = []
        seen: set = set()
        out: list = []
        for r in fts_rows + like_rows:
            c = r[0]
            if c not in seen:
                seen.add(c)
                out.append(c)
            if len(out) >= limit:
                break
        return out


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
    """内置会话记忆 Provider：包装 MemoryStore。

    注意：会话流水（turns）由 Agent 直接经 ``self.memory``（同一 MemoryStore）
    追加与管理，因此本 provider 的 ``sync_turn`` 为 no-op —— 交由 MemoryManager
    编排的外部 provider 去抽取/落库，避免同一轮对话在内置库里被重复写入两次。
    """

    name = "builtin"

    def __init__(self, path: str = "data/zhishu_memory.db", store: Optional[MemoryStore] = None):
        self.store = store or MemoryStore(path)

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str = "", **kwargs) -> None:
        pass

    def prefetch(self, query: str, *, owner: Optional[str] = None,
                 session_id: str = "") -> str:
        return ""

    def system_prompt_block(self, owner: Optional[str] = None) -> str:
        return ""

    def sync_turn(self, user_content: str, assistant_content: str = "", *,
                  owner: Optional[str] = None, session_id: str = "",
                  messages: Optional[list] = None) -> None:
        # 会话流水由 Agent 直接经 MemoryStore 管理（见 agent.py），此处不重复落库。
        return None

    def get_tool_schemas(self, owner: Optional[str] = None) -> list:
        return []

    def shutdown(self) -> None:
        return None
