"""智枢智能体 —— 本地向量库（SQLite + numpy 余弦相似度）。

零外部服务，内网离线可用。可选后端 milvus / pgvector / dm（接口一致）。
对外接口：add / search / count / clear / list_documents / get_document /
delete_document / doc_count。

文档级元数据（documents 表）与向量（vectors 表）分离存储：
  - documents：每个被入库的文档一行，记录标题/来源/类型/归属人/分块数/
    字符数/大小/创建时间/预览正文，供列表展示与删除。
  - vectors  ：每个文本分块一行，用于相似度检索。
两者通过 doc_id 关联。owner 为 NULL 表示「共享文档」，对所有用户可见、
可被任意用户检索；非 NULL 表示私有文档，仅归属人（及管理员）可见。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import List, Optional

import numpy as np

from .config import VectorStoreConfig

logger = logging.getLogger("zhishu.vector_store")


# 预览正文最多保存的字符数（避免大文件撑爆数据库）
_PREVIEW_CAP = 200_000


class VectorStore:
    def __init__(self, cfg: VectorStoreConfig):
        self.cfg = cfg
        self.backend = cfg.backend
        # 内存向量索引（首次检索时惰性加载，之后复用；add 时置脏重建）。
        # 避免每次检索都从数 GB 的库里整表回读（sqlite 同页含大 text/meta，
        # 即便只 SELECT vec 也会把整行读进内存，导致检索从毫秒退化到数秒乃至数十秒）。
        self._index = None          # list[(id, doc_id, np.ndarray vec)]
        self._owner_map = None      # doc_id -> owner(或 None)
        self._index_dirty = True
        # 全文检索可用性（FTS5）。未配置 embedding 模型时向量会降级为 hash
        # 伪向量（无语义能力），全文检索是唯一的检索主干，故此标记很关键。
        self._fts_available = False
        if self.backend == "sqlite":
            os.makedirs(os.path.dirname(cfg.path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(cfg.path, check_same_thread=False)
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT,
                    text TEXT,
                    meta TEXT,
                    vec BLOB
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    title TEXT,
                    source TEXT,
                    file_type TEXT,
                    owner TEXT,
                    chunk_count INTEGER DEFAULT 0,
                    char_count INTEGER DEFAULT 0,
                    size_bytes INTEGER DEFAULT 0,
                    created_at REAL,
                    updated_at REAL,
                    content TEXT
                )"""
            )
            self._conn.commit()
            # 老库迁移：raw_path 用于「重新解析」时回读原始文件
            cols = [r[1] for r in self._conn.execute(
                "PRAGMA table_info(documents)").fetchall()]
            if "raw_path" not in cols:
                self._conn.execute("ALTER TABLE documents ADD COLUMN raw_path TEXT")
                self._conn.commit()
            # 老库迁移：emb_sig 记录该分块所用的**向量空间签名**（模型/维度）。
            # 不同签名的向量不可互相比较：hash 伪向量与真语义向量混存会让检索
            # 结果失真，维度不同时 np.dot 还会直接抛 shape 异常。老库无签名的行
            # 按「未知」处理，检索时与当前签名不匹配则跳过（提示重新解析）。
            vcols = [r[1] for r in self._conn.execute(
                "PRAGMA table_info(vectors)").fetchall()]
            if "emb_sig" not in vcols:
                self._conn.execute("ALTER TABLE vectors ADD COLUMN emb_sig TEXT")
                self._conn.commit()
            # 全文检索（FTS5）：embedding 未配置时会静默降级为 hash 伪向量（无语义
            # 能力），全文检索是唯一的语义/关键词检索主干。用 trigram 分词以支持中文
            # 子串匹配（中文无天然词边界，trigram 比默认分词更可靠）。
            # 采用 external-content 模式：vectors_fts 不重复存文本，只索引 vectors(id,text)，
            # 通过触发器随 vectors 表增删改自动同步。
            try:
                self._conn.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS vectors_fts USING fts5(
                           text, content='vectors', content_rowid='id',
                           tokenize='trigram'
                       )"""
                )
                self._conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS vectors_ai AFTER INSERT ON vectors "
                    "BEGIN INSERT INTO vectors_fts(rowid, text) VALUES (new.id, new.text); END;")
                self._conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS vectors_ad AFTER DELETE ON vectors "
                    "BEGIN INSERT INTO vectors_fts(vectors_fts, rowid, text) "
                    "VALUES('delete', old.id, old.text); END;")
                self._conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS vectors_au AFTER UPDATE ON vectors "
                    "BEGIN INSERT INTO vectors_fts(vectors_fts, rowid, text) "
                    "VALUES('delete', old.id, old.text); "
                    "INSERT INTO vectors_fts(rowid, text) VALUES (new.id, new.text); END;")
                # 老库 / 已存在 FTS 表但内容为空：从 vectors 重建一次（幂等）
                v_cnt = self._conn.execute("SELECT count(*) FROM vectors").fetchone()[0]
                fts_cnt = self._conn.execute("SELECT count(*) FROM vectors_fts").fetchone()[0]
                if v_cnt > 0 and fts_cnt == 0:
                    self._conn.execute("INSERT INTO vectors_fts(vectors_fts) VALUES('rebuild')")
                self._conn.commit()
                self._fts_available = True
            except Exception as e:  # noqa: BLE001 —— FTS5 不可用（极老 sqlite）时优雅降级
                logger.warning("FTS5 不可用，全文检索已关闭：%s", e)
                self._fts_available = False
        else:
            # 占位：milvus / pgvector / dm 接入点（保持接口一致）
            raise NotImplementedError(
                f"向量库后端 '{self.backend}' 需安装对应驱动并接入，"
                f"当前默认 sqlite 已满足内网离线需求。"
            )

    # ------------------------- 写入 -------------------------
    def add(
        self,
        doc_id: str,
        chunks: List[str],
        vectors: List[List[float]],
        meta: Optional[dict] = None,
        emb_sig: Optional[str] = None,
    ):
        meta = meta or {}
        # 安全/一致性：同一 doc_id 重复入库时，先清掉旧分块向量再写入，
        # 避免 documents 行被 UPSERT（归属/内容已更新）而 vectors 表残留
        # 旧内容分块 —— 旧实现会导致检索命中已被覆盖文档的历史内容（串库）。
        self._conn.execute("DELETE FROM vectors WHERE doc_id=?", (doc_id,))
        cur = self._conn.executemany(
            "INSERT INTO vectors (doc_id, text, meta, vec, emb_sig) VALUES (?,?,?,?,?)",
            [
                (doc_id, text, json.dumps(meta, ensure_ascii=False),
                 np.array(v, dtype=np.float32).tobytes(), emb_sig)
                for text, v in zip(chunks, vectors)
            ],
        )
        # 文档级元数据 upsert
        now = time.time()
        content = meta.get("content_preview") or ""
        if len(content) > _PREVIEW_CAP:
            content = content[:_PREVIEW_CAP]
        existing = self._conn.execute(
            "SELECT created_at FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if existing:
            self._conn.execute(
                """UPDATE documents SET title=?, source=?, file_type=?, owner=?,
                       chunk_count=?, char_count=?, size_bytes=?, updated_at=?, content=?,
                       raw_path=?
                   WHERE doc_id=?""",
                (
                    meta.get("title", doc_id), meta.get("source"),
                    meta.get("file_type"), meta.get("owner"),
                    len(chunks), meta.get("char_count", 0), meta.get("size", 0),
                    now, content, meta.get("raw_path"), doc_id,
                ),
            )
        else:
            self._conn.execute(
                """INSERT INTO documents
                   (doc_id, title, source, file_type, owner, chunk_count, char_count,
                    size_bytes, created_at, updated_at, content, raw_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    doc_id, meta.get("title", doc_id), meta.get("source"),
                    meta.get("file_type"), meta.get("owner"),
                    len(chunks), meta.get("char_count", 0), meta.get("size", 0),
                    now, now, content, meta.get("raw_path"),
                ),
            )
        self._conn.commit()
        self._index_dirty = True  # 新增向量后内存索引失效，下次检索重建
        return cur.rowcount

    # ------------------------- 检索 -------------------------
    def _ensure_index(self):
        """惰性加载内存向量索引（含 doc_id→owner 映射），仅在首次或 add 后置脏时重建。"""
        if (not self._index_dirty) and (self._index is not None):
            return
        rows = self._conn.execute(
            "SELECT id, doc_id, vec, emb_sig FROM vectors"
        ).fetchall()
        # copy()：frombuffer 为只读视图，底层 bytes 在函数返回后可能被回收
        self._index = [
            (r[0], r[1], np.frombuffer(r[2], dtype=np.float32).copy(), r[3] or "")
            for r in rows
        ]
        own_rows = self._conn.execute(
            "SELECT doc_id, owner FROM documents"
        ).fetchall()
        self._owner_map = {r[0]: r[1] for r in own_rows}
        self._index_dirty = False

    def search(self, query_vec: List[float], top_k: int = 5,
               owner: Optional[str] = None,
               emb_sig: Optional[str] = None) -> List[dict]:
        self._ensure_index()
        q = np.array(query_vec, dtype=np.float32)
        qn = np.linalg.norm(q)
        if qn > 0:
            q = q / qn
        # 内存索引内计算余弦相似度（O(n)，n=向量数，通常数千级，亚秒级）
        cand = self._index
        if owner is not None:
            # owner 过滤：仅本人文档 + owner IS NULL 的共享文档。
            # 防御：doc_id 必须存在于 owner_map 中，否则视为未知/脏数据一律隐藏
            # （宁可漏显也不外泄），杜绝因内存索引未及时重建导致私有文档被误判为
            # 共享文档而跨用户泄露。
            cand = [
                (i, d, v, s) for (i, d, v, s) in self._index
                if d in self._owner_map
                and (self._owner_map[d] == owner or self._owner_map[d] is None)
            ]
        # 向量空间隔离：只与**同签名**的向量比较。
        #   * 签名不同 = 不同模型/不同维度/hash 伪向量，余弦相似度无意义；
        #   * 老库（emb_sig 为空）视为未知签名，按「维度相同才比」的宽松规则放行，
        #     保证升级后历史文档仍可检索，不需要强制重建索引。
        if emb_sig:
            cand = [c for c in cand if (c[3] == emb_sig) or (not c[3])]
        scored = []
        qdim = q.shape[0]
        for _id, doc_id, v, _sig in cand:
            # 纵深防御：维度不一致直接跳过（历史脏数据 / 未打签名的混存向量），
            # 否则 np.dot 会抛 shape 异常，整次检索失败。
            if v.shape[0] != qdim:
                continue
            vn = np.linalg.norm(v)
            if vn > 0:
                v = v / vn
            sim = float(np.dot(q, v))
            scored.append((sim, _id, doc_id))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]
        if not top:
            return []
        # 仅回查 top-k 命中的大字段（text/meta），避免全表拉取
        ids = [t[1] for t in top]
        placeholders = ",".join("?" * len(ids))
        meta_rows = self._conn.execute(
            f"SELECT id, doc_id, text, meta FROM vectors WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        meta_map = {r[0]: (r[2], r[3]) for r in meta_rows}
        out = []
        for sim, _id, doc_id in top:
            text, meta = meta_map.get(_id, ("", "{}"))
            out.append({
                "id": _id, "doc_id": doc_id, "text": text,
                "meta": json.loads(meta or "{}"), "score": sim,
            })
        return out

    def fts_search(self, query: str, top_k: int = 10,
                   owner: Optional[str] = None) -> List[dict]:
        """全文检索（FTS5 trigram + bm25），作为未配置 embedding 时的检索主干。

        返回按相关度排序的命中列表，每条含 id/doc_id/text/meta/score。
        score 为「越高越相关」：FTS 命中取 -bm25（bm25 本身为负分），LIKE 兜底为 0.0。

        触发兜底：查询串 < 3 字符（trigram 对短串不友好）或 FTS 无命中时，
        退化为对 vectors.text 的 LIKE 子串匹配，保证中文关键词仍可命中。
        """
        if not self._fts_available:
            return []
        q = (query or "").strip()
        if not q:
            return []
        safe = q.replace('"', '""')
        fts_query = f'"{safe}"'
        params: list = [fts_query]
        join = ""
        where = ["vectors_fts MATCH ?"]
        if owner is not None:
            join = "LEFT JOIN documents d ON v.doc_id = d.doc_id"
            where.append("(d.owner = ? OR d.owner IS NULL)")
            params.append(owner)
        sql = (
            "SELECT v.id, v.doc_id, v.text, v.meta, bm25(vectors_fts) AS rank "
            f"FROM vectors_fts f JOIN vectors v ON v.id = f.rowid {join} "
            "WHERE " + " AND ".join(where) +
            " ORDER BY rank LIMIT ?"
        )
        params.append(top_k * 3)
        rows: list = []
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except Exception:  # noqa: BLE001
            rows = []
        if len(q) < 3 or not rows:
            # 短查询 / 无命中：LIKE 兜底（中文子串）
            lparams: list = [f"%{q}%"]
            lwhere = ["v.text LIKE ?"]
            if owner is not None:
                lwhere.append("(d.owner = ? OR d.owner IS NULL)")
                lparams.append(owner)
            lsql = "SELECT v.id, v.doc_id, v.text, v.meta FROM vectors v "
            if owner is not None:
                lsql += "LEFT JOIN documents d ON v.doc_id = d.doc_id "
            lsql += "WHERE " + " AND ".join(lwhere) + " LIMIT ?"
            lparams.append(top_k * 3)
            try:
                rows = [(r[0], r[1], r[2], r[3], 0.0)
                        for r in self._conn.execute(lsql, lparams).fetchall()]
            except Exception:  # noqa: BLE001
                rows = []
        out = []
        for r in rows:
            _id, doc_id, text, meta, rank = r[0], r[1], r[2], r[3], r[4]
            out.append({
                "id": _id, "doc_id": doc_id, "text": text,
                "meta": json.loads(meta or "{}"),
                # bm25 为负分（越负越相关）→ 取反使「越高越好」；LIKE 兜底为 0.0
                "score": float(-rank) if rank else 0.0,
            })
        return out

    def signature_stats(self, current_sig: Optional[str] = None) -> dict:
        """按向量空间签名统计分块数，用于暴露「需要重新解析」的陈旧向量。

        返回 {"by_signature": {sig: n}, "stale": n}；stale 指签名与当前不一致
        且非空的分块（这些分块在当前 embedding 配置下检索不到）。
        """
        rows = self._conn.execute(
            "SELECT COALESCE(emb_sig,''), COUNT(*) FROM vectors GROUP BY 1"
        ).fetchall()
        by_sig = {(r[0] or "(未标记)"): r[1] for r in rows}
        stale = 0
        if current_sig:
            for r in rows:
                if r[0] and r[0] != current_sig:
                    stale += r[1]
        return {"by_signature": by_sig, "stale": stale}

    def count(self, owner: Optional[str] = None) -> int:
        if owner is None:
            return self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        return self._conn.execute(
            """SELECT COUNT(*) FROM vectors v
               LEFT JOIN documents d ON v.doc_id = d.doc_id
               WHERE d.owner = ? OR d.owner IS NULL""",
            (owner,),
        ).fetchone()[0]

    # ------------------------- 文档级管理 -------------------------
    def list_documents(self, owner: Optional[str] = None,
                       limit: int = 200, offset: int = 0,
                       q: Optional[str] = None) -> List[dict]:
        where = []
        params: list = []
        if owner is not None:
            where.append("(owner = ? OR owner IS NULL)")
            params.append(owner)
        if q:
            where.append("(title LIKE ? OR source LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        ws = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self._conn.execute(
            f"""SELECT doc_id, title, source, file_type, owner, chunk_count,
                       char_count, size_bytes, created_at, updated_at, raw_path
                FROM documents {ws}
                ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "doc_id": r[0], "title": r[1] or r[0], "source": r[2],
                "file_type": r[3], "owner": r[4], "chunk_count": r[5],
                "char_count": r[6], "size_bytes": r[7],
                "created_at": r[8], "updated_at": r[9], "raw_path": r[10] or "",
            })
        return out

    def get_document(self, doc_id: str, owner: Optional[str] = None) -> Optional[dict]:
        row = self._conn.execute(
            """SELECT doc_id, title, source, file_type, owner, chunk_count,
                      char_count, size_bytes, created_at, updated_at, content, raw_path
               FROM documents WHERE doc_id=?""",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        doc = {
            "doc_id": row[0], "title": row[1] or row[0], "source": row[2],
            "file_type": row[3], "owner": row[4], "chunk_count": row[5],
            "char_count": row[6], "size_bytes": row[7],
            "created_at": row[8], "updated_at": row[9],
            "content": row[10] or "", "raw_path": row[11] or "",
        }
        if owner is not None and doc["owner"] is not None and doc["owner"] != owner:
            return None
        return doc

    def delete_document(self, doc_id: str, owner: Optional[str] = None) -> bool:
        row = self._conn.execute(
            "SELECT owner, raw_path FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if not row:
            return False
        # owner=None 视为管理员，可删任意；owner 为空的公共文档仅管理员可删；
        # 否则仅能删自己的私有文档
        if owner is not None and (row[0] or "") != owner:
            return False
        # 清理原始文件（重新解析用），失败不影响删除
        if row[1]:
            try:
                os.remove(row[1])
            except OSError:
                pass
        self._conn.execute("DELETE FROM vectors WHERE doc_id=?", (doc_id,))
        self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        self._conn.commit()
        self._index_dirty = True  # 删除向量后内存索引失效，下次检索重建
        return True

    def doc_count(self, owner: Optional[str] = None,
                  q: Optional[str] = None) -> int:
        where = []
        params: list = []
        if owner is not None:
            where.append("(owner = ? OR owner IS NULL)")
            params.append(owner)
        if q:
            where.append("(title LIKE ? OR source LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        ws = ("WHERE " + " AND ".join(where)) if where else ""
        return self._conn.execute(
            f"SELECT COUNT(*) FROM documents {ws}", params
        ).fetchone()[0]

    def clear(self, doc_id: str = None):
        if doc_id:
            self._conn.execute("DELETE FROM vectors WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        else:
            self._conn.execute("DELETE FROM vectors")
            self._conn.execute("DELETE FROM documents")
        self._conn.commit()
