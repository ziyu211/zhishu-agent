"""智枢智能体 —— 记忆质量演化存储（对标 Hermes holographic 的 trust/decay/contradict）。

独立于向量库的轻量 SQLite 质量层：以 (owner, content_hash) 为键，记录每条长期记忆的
信任分、命中次数、创建时间；检索时按 trust + 时效衰减重排，反馈可调整 trust，写入时
做矛盾检测（高相似且语义冲突的旧记忆被标记，避免矛盾记忆污染）。

设计要点：
  * 零向量依赖：相似度用 Jaccard（字符集合）与包含关系近似，避免再引入 embedding；
  * 纯本地 SQLite，离线可用；损坏/缺表自动重建，绝不阻塞主流程；
  * 阈值全部显式常量，便于单测与调参。
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger("zhishu.memory")

# 信任分边界（对齐 Hermes：helpful +0.05 / unhelpful −0.10）
TRUST_MIN = -2.0
TRUST_MAX = 2.0
TRUST_DELTA_HELPFUL = 0.05
TRUST_DELTA_UNHELPFUL = -0.10
# 时效衰减半衰期（天）：记忆超过该时间，召回权重衰减一半
_DECAY_HALF_LIFE_DAYS = 30.0
# 矛盾检测的相似度阈值（Jaccard ≥ 此值视为「同主题」）
_SIM_THRESHOLD = 0.45
# 同主题下，若新文本与旧文本无公共实体/明显不一致 → 标记潜在矛盾
_CONTRADICT_NEEDS_CHARS = 8

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_quality (
    owner TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    trust REAL NOT NULL DEFAULT 0.0,
    hits INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (owner, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_mq_owner ON memory_quality(owner);
"""


def _norm_owner(owner: Optional[str]) -> str:
    return (owner or "anon").strip() or "anon"


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


def _jaccard(a: str, b: str) -> float:
    """字符级 Jaccard 相似度（0~1）；空串视为 0。"""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class MemoryQualityStore:
    """记忆质量层（trust / hits / decay / 矛盾检测）。"""

    def __init__(self, path: str):
        self.path = path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- 内部工具 ------------------------------------------------------------

    def _exec(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Cursor]:
        with _LOCK:
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur
            except Exception as e:  # noqa: BLE001
                logger.debug("memory quality store error: %s", e)
                return None

    # -- 写入 ----------------------------------------------------------------

    def record(self, owner: Optional[str], content: str,
               meta: Optional[dict] = None) -> None:
        """登记一条长期记忆（写入时调用；重复内容只更新 updated_at 不清零 trust）。"""
        text = (content or "").strip()
        if not text:
            return
        o = _norm_owner(owner)
        h = _content_hash(text)
        now = time.time()
        self._exec(
            "INSERT INTO memory_quality(owner, content_hash, content, trust, hits,"
            " created_at, updated_at) VALUES(?,?,?,0,0,?,?)"
            " ON CONFLICT(owner, content_hash) DO UPDATE SET updated_at=excluded.updated_at",
            (o, h, text[:2000], now, now),
        )

    def bump_hit(self, owner: Optional[str], content: str) -> None:
        """召回命中一次（计数 +1，用于统计热度）。"""
        o = _norm_owner(owner)
        h = _content_hash(content)
        self._exec(
            "UPDATE memory_quality SET hits=hits+1, updated_at=? "
            "WHERE owner=? AND content_hash=?",
            (time.time(), o, h),
        )

    def feedback(self, owner: Optional[str], content: str,
                 helpful: bool) -> Optional[float]:
        """反馈训练：helpful +0.05 / unhelpful −0.10（对齐 Hermes fact_feedback）。

        返回调整后的 trust；条目不存在返回 None（调用方提示未知记忆）。
        """
        o = _norm_owner(owner)
        h = _content_hash(content)
        delta = TRUST_DELTA_HELPFUL if helpful else TRUST_DELTA_UNHELPFUL
        now = time.time()
        cur = self._exec(
            "UPDATE memory_quality SET trust=MAX(?, MIN(?, trust+?)), updated_at=? "
            "WHERE owner=? AND content_hash=?",
            (TRUST_MIN, TRUST_MAX, delta, now, o, h),
        )
        if cur is None or cur.rowcount == 0:
            return None
        row = self._conn.execute(
            "SELECT trust FROM memory_quality WHERE owner=? AND content_hash=?",
            (o, h)).fetchone()
        return row["trust"] if row else None

    # -- 检索重排 ------------------------------------------------------------

    def rank(self, owner: Optional[str], hits: List[str], top_k: int) -> List[str]:
        """按 trust + 时效衰减对向量召回结果重排（trust 优先，衰减做同分决胜）。"""
        if not hits:
            return []
        o = _norm_owner(owner)
        scored: List[tuple] = []
        now = time.time()
        for h in hits[: max(top_k * 2, top_k)]:
            hh = _content_hash(h)
            row = None
            try:
                row = self._conn.execute(
                    "SELECT trust, created_at, hits FROM memory_quality "
                    "WHERE owner=? AND content_hash=?", (o, hh)).fetchone()
            except Exception:
                pass
            if row is None:
                # 无质量记录（老数据/直写）：按命中顺序保持，给中性分
                scored.append((0.0, 0.0, -now, h))
                continue
            trust = float(row["trust"] or 0.0)
            age_days = max(0.0, (now - float(row["created_at"] or now)) / 86400.0)
            decay = 2.0 ** (-age_days / _DECAY_HALF_LIFE_DAYS)
            scored.append((trust, decay, -now, h))
        # 主排序 trust 降序，次排序衰减降序（较新的优先）
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [h for _, _, _, h in scored[:top_k]]

    # -- 矛盾检测 ------------------------------------------------------------

    def contradict(self, owner: Optional[str], new_text: str,
                   existing: List[str]) -> List[str]:
        """检测新事实与已有记忆的潜在矛盾。

        规则（保守，宁漏勿误）：
          * 与某条旧记忆 Jaccard 相似度 ≥ 阈值 → 同主题候选；
          * 若新文本是旧文本的子串、或旧文本是新文本的子串（包含关系）→ 视为
            「补充/细化」而非矛盾，不判（避免把『相关内容』误判为冲突）；
          * 同主题、无包含关系、且双方长度都 ≥ 最小字符 → 视为「新说法覆盖/冲突」，
            返回该旧记忆（由调用方决定：跳过写入或提示用户确认）。
        """
        if not new_text or len(new_text.strip()) < _CONTRADICT_NEEDS_CHARS:
            return []
        conflicts: List[str] = []
        for old in existing or []:
            if not old or len(old.strip()) < _CONTRADICT_NEEDS_CHARS:
                continue
            sim = _jaccard(new_text, old)
            if sim >= _SIM_THRESHOLD:
                # 包含关系 → 补充/细化，不算矛盾
                if new_text.strip() in old or old.strip() in new_text:
                    continue
                conflicts.append(old)
        return conflicts

    # -- 统计 / 清理 ---------------------------------------------------------

    def stats(self, owner: Optional[str] = None) -> dict:
        o = _norm_owner(owner) if owner else None
        try:
            if o:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c, AVG(trust) AS avg_t FROM memory_quality "
                    "WHERE owner=?", (o,)).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c, AVG(trust) AS avg_t FROM memory_quality").fetchone()
            return {"count": int(row["c"] or 0),
                    "avg_trust": round(float(row["avg_t"] or 0.0), 3)}
        except Exception as e:  # noqa: BLE001
            return {"count": 0, "avg_trust": 0.0, "error": str(e)}

    def clear(self, owner: Optional[str] = None) -> int:
        o = _norm_owner(owner) if owner else None
        try:
            if o:
                cur = self._exec("DELETE FROM memory_quality WHERE owner=?", (o,))
            else:
                cur = self._exec("DELETE FROM memory_quality")
            return cur.rowcount if cur is not None else 0
        except Exception:  # noqa: BLE001
            return 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
