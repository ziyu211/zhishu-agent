"""智枢智能体 —— 知识图谱层（关键词共现网络，离线、零 token）。

设计目标：在现有向量 RAG 之上，构建一个「实体/关键词 × 共现关系」的网络，
前端用 ECharts 力导向图渲染，效果类似 Obsidian 本地关系图 / 网传「导入红楼梦
生成人物关系图」。

提取逻辑（入库时增量执行）：
  1. 中文分词（jieba，缺失则退化正则）+ 停用词过滤 → 关键词节点；
  2. 同一分块（段落/句子）内关键词两两共现 → 边，权重 = 共现分块数；
  3. 持久化于 data_dir/zhishu_kg.db（SQLite），与 zhishu_vector.db 平行。

权限：节点/边记录归属 owner；普通用户仅见自己 + 共享（owner=None）文档贡献的
子图，admin 传 owner=None 可见全量。
"""
from __future__ import annotations

import os
import re
import json
import sqlite3
import logging
from collections import Counter

logger = logging.getLogger("zhishu.kgraph")

try:
    import jieba
    import jieba.posseg as _posseg
    _HAS_JIEBA = True
except Exception:  # pragma: no cover - jieba 缺失时退化
    _HAS_JIEBA = False

# 保留的词性（实体/概念类），其余（虚词、标点、助词）丢弃
_KEEP_FLAGS = (
    "n", "nr", "ns", "nt", "nz", "nl", "ng",  # 名词 / 人名 / 地名 / 机构 / 其他名词
    "v", "vn", "vd",                           # 动词
    "a", "ad", "an", "i", "j", "l",            # 形容词 / 成语 / 简称略语
)
# 也保留 2~4 字且非停用词的「未知词」（jieba 未登录但像专有名词）
_MIN_KW_LEN = 2
_MAX_KW_LEN = 8

# 内置停用词（中英文常见虚词 + 标点 + 数字单位）
_STOPWORDS = set(
    "的 了 和 是 在 我 有 他 这 中 大 来 上 个 到 说 们 为 子 也 你 得 着 下 就 还 与 及 或 把 被 让 从 向 对 等 都 而 那 它 她 其 此 之 其 已 又 但 因 若 即 且 则 并 如 若 给 使 叫 做 作 没 无 不 不是 没有 一种 一样 一样 一些 这样 那样 怎么 什么 怎么 为什么 如何 哪些 多少 自己 我们 你们 他们 它们 大家 别人 别人 一切 某种 某些 任何 每个 各自 一切 全部 所有 部分 一些 一点 有些 其他 此外 另外 例如 比如 比如 其中 之后 之前 以前 以后 现在 目前 然后 于是 因此 所以 因为 由于 如果 虽然 但是 然而 并且 而且 以及 或者 只有 只要 除非 无论 即使 尽管 可是 不过 而且 反而 甚至 尤其 特别 十分 非常 比较 更加 有点 稍微 大概 也许 恐怕 似乎 好像 的确 实在 根本 简直 几乎 差不多 反正 毕竟 终究 究竟 到底 究竟 难怪 怪不得 原来 其实 当然 自然 固然 诚然 幸而 幸亏 可惜 好在 不料 反而 否则 不然 不如 宁可 宁愿 索性 简直 未免 未免 未免 罢了 而已 何况 况且 再说 反正 其实 只是 不过 但是 可是 然而 不过 只是".split()
)
_PUNCT = set("，。、；：？！“”‘’（）《》〈〉【】[]{}…—·.,;:?!\"'()<>/\\|-_=+*&^%$#@~`")
# 边界杂质字符：出现在关键词首尾的单字（如「和黛玉」中的「和」）应剔除
_STOP_EDGE = set("和与之及或等其那这你我他她它们的可被把对于从向到在上中下里外内前后来去给使叫做了着过")
_ENG_STOP = set(
    "the a an and or of to in on for with at by from as is are was were be been being this that these those it its he she they we you i my your our their his her them us me not no yes can will would should could may might must do does did have has had if then else when where what which who whom how why all any some more most other into out up down over under again about than so such only own same too very just also even still yet already ever never once soon often always".split()
)


def _is_stopword(w: str) -> bool:
    if not w:
        return True
    low = w.lower()
    if low in _STOPWORDS or low in _ENG_STOP:
        return True
    if set(w) & _PUNCT:
        return True
    # 纯数字 / 纯标点 / 含空白
    if re.fullmatch(r"[\d\s\W_]+", w):
        return True
    return False


def _clean_kw(w: str) -> str | None:
    """剔除首尾杂质字符（如「和」「与」粘连的「和黛玉」）。返回清洗后词或 None。"""
    w = w.strip()
    if not w:
        return None
    # 去掉首尾的单字停用/杂质字符
    while len(w) > 1 and w[0] in _STOP_EDGE:
        w = w[1:]
    while len(w) > 1 and w[-1] in _STOP_EDGE:
        w = w[:-1]
    if len(w) < _MIN_KW_LEN:
        return None
    return w


def _extract_keywords(text: str, top_k: int = 60) -> list[str]:
    """从单篇文本提取关键词列表（已去重）。"""
    if _HAS_JIEBA:
        try:
            words = []
            for w, flag in _posseg.cut(text):
                w = w.strip()
                if not w or _is_stopword(w):
                    continue
                if flag in _KEEP_FLAGS:
                    words.append(w)
                elif _MIN_KW_LEN <= len(w) <= _MAX_KW_LEN and re.fullmatch(r"[\u4e00-\u9fff]+", w):
                    # 连续汉字的未登录词，当作候选专有名词保留
                    words.append(w)
            # 边界清洗（剔除「和黛玉」类粘连）
            words = [c for c in (_clean_kw(w) for w in words) if c]
            # 频率统计取高频
            cnt = Counter(words)
            return [w for w, _ in cnt.most_common(top_k)]
        except Exception as e:  # 分词异常退化
            logger.warning("jieba 提取失败，退化正则：%s", e)
    # 退化：正则切连续中文 2~4 字 + 频次
    han = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
    han = [c for c in (_clean_kw(w) for w in han) if c]
    cnt = Counter(w for w in han if not _is_stopword(w))
    return [w for w, _ in cnt.most_common(top_k)]


def _split_chunks(text: str, max_chars: int = 400) -> list[str]:
    """按段落/句子把文本切成小段，供共现计算。"""
    parts = re.split(r"\n+|\u3000+", text)
    chunks: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 段落过长再按句号切
        if len(p) > max_chars:
            for s in re.split(r"[。！？!?；;]", p):
                s = s.strip()
                if s:
                    chunks.append(s)
        else:
            chunks.append(p)
    return chunks


class KnowledgeGraph:
    """关键词共现图谱（SQLite 持久化）。"""

    def __init__(self, data_dir: str | None):
        self.enabled = bool(data_dir)
        if not data_dir:
            self._conn = None
            return
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "zhishu_kg.db")
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kg_nodes (
                name    TEXT PRIMARY KEY,
                freq    INTEGER NOT NULL DEFAULT 0,
                doc_count INTEGER NOT NULL DEFAULT 0,
                owners  TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS kg_edges (
                src     TEXT NOT NULL,
                dst     TEXT NOT NULL,
                weight  INTEGER NOT NULL DEFAULT 0,
                owners  TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (src, dst)
            );
            CREATE TABLE IF NOT EXISTS kg_doc (
                doc_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_edges_src ON kg_edges(src);
            CREATE INDEX IF NOT EXISTS idx_edges_dst ON kg_edges(dst);
            """
        )
        c.commit()

    # ------------------------- 写入 -------------------------
    def analyze_document(self, doc_id: str, text: str, owner: str | None = None):
        if not self.enabled or not text:
            return
        try:
            chunks = _split_chunks(text)
            if not chunks:
                return
            # 每篇文档的关键词集合（按 chunk 聚合）
            doc_kw_counter: Counter = Counter()
            # 每篇文档的边共现： (a,b) -> 共现 chunk 数
            doc_edge_counter: Counter = Counter()
            for ch in chunks:
                kws = _extract_keywords(ch, top_k=40)
                # 去重后的关键词集合用于共现
                uniq = list(dict.fromkeys(kws))  # 保序去重
                doc_kw_counter.update(uniq)
                # 两两共现（同 chunk 出现即 +1）
                n = len(uniq)
                for i in range(n):
                    for j in range(i + 1, n):
                        a, b = uniq[i], uniq[j]
                        key = (a, b) if a <= b else (b, a)
                        doc_edge_counter[key] += 1

            # 落库（增量）
            c = self._conn
            owner_json = json.dumps([owner] if owner is not None else [None], ensure_ascii=False)
            # 节点
            for name, cnt in doc_kw_counter.items():
                row = c.execute("SELECT freq,doc_count,owners FROM kg_nodes WHERE name=?", (name,)).fetchone()
                if row is None:
                    owners = [owner]
                    c.execute(
                        "INSERT INTO kg_nodes(name,freq,doc_count,owners) VALUES(?,?,?,?)",
                        (name, cnt, 1, json.dumps(owners, ensure_ascii=False)),
                    )
                else:
                    freq, doc_count, owns = row
                    owns = json.loads(owns) if owns else []
                    if owner not in owns:
                        owns.append(owner)
                        doc_count += 1
                    c.execute(
                        "UPDATE kg_nodes SET freq=?,doc_count=?,owners=? WHERE name=?",
                        (freq + cnt, doc_count, json.dumps(owns, ensure_ascii=False), name),
                    )
            # 边
            for (a, b), w in doc_edge_counter.items():
                row = c.execute("SELECT weight,owners FROM kg_edges WHERE src=? AND dst=?", (a, b)).fetchone()
                if row is None:
                    c.execute(
                        "INSERT INTO kg_edges(src,dst,weight,owners) VALUES(?,?,?,?)",
                        (a, b, w, json.dumps([owner], ensure_ascii=False)),
                    )
                else:
                    weight, owns = row
                    owns = json.loads(owns) if owns else []
                    if owner not in owns:
                        owns.append(owner)
                    c.execute(
                        "UPDATE kg_edges SET weight=?,owners=? WHERE src=? AND dst=?",
                        (weight + w, json.dumps(owns, ensure_ascii=False), a, b),
                    )
            # 文档索引（用于删除回退）
            payload = json.dumps(
                {"kws": dict(doc_kw_counter), "edges": {f"{a}|{b}": w for (a, b), w in doc_edge_counter.items()}},
                ensure_ascii=False,
            )
            c.execute(
                "INSERT INTO kg_doc(doc_id,payload) VALUES(?,?) ON CONFLICT(doc_id) DO UPDATE SET payload=excluded.payload",
                (doc_id, payload),
            )
            c.commit()
        except Exception as e:
            logger.error("analyze_document 失败 doc_id=%s: %s", doc_id, e)

    def remove_document(self, doc_id: str):
        if not self.enabled:
            return
        try:
            c = self._conn
            row = c.execute("SELECT payload FROM kg_doc WHERE doc_id=?", (doc_id,)).fetchone()
            if not row:
                return
            payload = json.loads(row[0])
            kws: dict = payload.get("kws", {})
            edges: dict = payload.get("edges", {})
            # 节点回退
            for name, cnt in kws.items():
                row = c.execute("SELECT freq,doc_count,owners FROM kg_nodes WHERE name=?", (name,)).fetchone()
                if not row:
                    continue
                freq, doc_count, owns = row
                owns = json.loads(owns) if owns else []
                # 去掉该文档 owner 标记：doc_count 简易处理——仅当 owners 中该 owner 存在时减 1
                # 注：多文档同 owner 时 doc_count 近似，可接受
                new_freq = max(0, freq - cnt)
                new_doc = max(0, doc_count - 1)
                if new_freq <= 0:
                    c.execute("DELETE FROM kg_nodes WHERE name=?", (name,))
                else:
                    c.execute("UPDATE kg_nodes SET freq=?,doc_count=? WHERE name=?", (new_freq, new_doc, name))
            # 边回退
            for pair, w in edges.items():
                a, b = pair.split("|", 1)
                row = c.execute("SELECT weight FROM kg_edges WHERE src=? AND dst=?", (a, b)).fetchone()
                if not row:
                    continue
                new_w = max(0, row[0] - w)
                if new_w <= 0:
                    c.execute("DELETE FROM kg_edges WHERE src=? AND dst=?", (a, b))
                else:
                    c.execute("UPDATE kg_edges SET weight=? WHERE src=? AND dst=?", (new_w, a, b))
            c.execute("DELETE FROM kg_doc WHERE doc_id=?", (doc_id,))
            c.commit()
        except Exception as e:
            logger.error("remove_document 失败 doc_id=%s: %s", doc_id, e)

    # ------------------------- 读取 -------------------------
    def get_graph(self, owner: str | None = None, limit: int = 300, min_weight: int = 1,
                   max_edges: int = 2000) -> dict:
        if not self.enabled:
            return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0}}
        c = self._conn
        # 节点（按 freq 降序），owner 过滤
        rows = c.execute(
            "SELECT name,freq,doc_count FROM kg_nodes ORDER BY freq DESC LIMIT ?", (limit * 3,)
        ).fetchall()
        if owner is not None:
            # 仅保留 owners 含 owner 或 None(共享) 的节点
            filt = []
            for name, freq, dc in rows:
                owns = json.loads(
                    c.execute("SELECT owners FROM kg_nodes WHERE name=?", (name,)).fetchone()[0] or "[]"
                )
                if owner in owns or None in owns:
                    filt.append((name, freq, dc))
            rows = filt
        rows = rows[:limit]
        node_names = {r[0] for r in rows}
        nodes = [
            {"name": r[0], "freq": r[1], "doc_count": r[2]} for r in rows
        ]
        # 边（两端都在节点集内，且 owner 过滤）
        edges = []
        if node_names:
            placeholders = ",".join("?" * len(node_names))
            erows = c.execute(
                f"SELECT src,dst,weight,owners FROM kg_edges WHERE src IN ({placeholders}) AND dst IN ({placeholders})",
                list(node_names) + list(node_names),
            ).fetchall()
            for src, dst, weight, owns in erows:
                if weight < min_weight:
                    continue
                if owner is not None:
                    ol = json.loads(owns or "[]")
                    if owner not in ol and None not in ol:
                        continue
                edges.append({"source": src, "target": dst, "weight": weight})
        # 按权重降序截断，避免超大语料把前端力导向图卡死
        edges.sort(key=lambda e: -e["weight"])
        edges = edges[:max_edges]
        total_nodes = c.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
        total_edges = c.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {"nodes": total_nodes, "edges": total_edges, "returned_nodes": len(nodes), "returned_edges": len(edges)},
        }

    def node_count(self) -> int:
        if not self.enabled:
            return 0
        return self._conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
