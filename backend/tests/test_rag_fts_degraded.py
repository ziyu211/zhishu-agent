"""回归测试：v1.0.62 Embedding 降级治理（参照 Hermes 检索策略）。

核心诉求：未配置 Embedding 模型时，智枢过去会**静默**降级为 hash 伪向量冒充语义检索，
导致 RAG / 长期记忆「名存实亡、Agent 变笨」而无人察觉。本次改造：
  * 全文检索（FTS5 trigram + bm25）作为未配置 embedding 时的唯一主干，始终可用；
  * 语义向量检索仅在真正可用时参与加权融合；
  * 显式暴露 unconfigured / semantic_available / retrieval_mode 给前端如实告知降级。

覆盖：
  1. EmbeddingEngine 配置反射：unconfigured / semantic_available 正确识别「未配置模型」；
  2. VectorStore.fts_search：中文子串经 FTS5 命中、<3 字符经 LIKE 兜底、owner 隔离；
  3. KnowledgeBase._hybrid_fuse：降级（向量为空）时全文检索独占；
  4. KnowledgeBase.stats：未配置时 retrieval_mode=='fts' 且透传 unconfigured 标记。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.config import EmbeddingConfig, VectorStoreConfig
from zhishu.core.embedding import EmbeddingEngine
from zhishu.core.rag import KnowledgeBase
from zhishu.core.vector_store import VectorStore

passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {msg}")
    else:
        failed += 1
        print(f"  [FAIL] {msg}")


# --------------------------- 1. Embedding 配置反射 ---------------------------
def test_embedding_unconfigured():
    # backend=provider 但未配 embed_model → 静默降级陷阱
    ec = EmbeddingConfig(backend="provider", embed_model="")
    eng = EmbeddingEngine(ec)
    check(eng.unconfigured is True,
          "backend=provider 且 embed_model='' → unconfigured=True（命中静默降级陷阱）")
    check(eng.semantic_available is False,
          "未配置模型 → semantic_available=False（向量无语义能力）")

    # backend=local → 真语义后端，不属未配置
    ec2 = EmbeddingConfig(backend="local", embed_model="")
    eng2 = EmbeddingEngine(ec2)
    check(eng2.unconfigured is False,
          "backend=local → unconfigured=False")
    check(eng2.semantic_available is True,
          "backend=local → semantic_available=True（具备语义能力）")

    # backend=provider 且配了模型 → 真语义 + 非未配置
    ec3 = EmbeddingConfig(backend="provider", embed_model="text-embedding-3-small")
    eng3 = EmbeddingEngine(ec3)
    check(eng3.unconfigured is False,
          "backend=provider 且已配 embed_model → unconfigured=False")
    check(eng3.semantic_available is True,
          "已配模型 → semantic_available=True")


# --------------------------- 2. FTS 中文检索 ---------------------------
def _new_store():
    d = tempfile.mkdtemp()
    cfg = VectorStoreConfig(backend="sqlite", path=os.path.join(d, "kb.db"))
    store = VectorStore(cfg)
    return d, store


def _add_chunks(store, doc_id, chunks, owner=None):
    dummy = [[0.1] * 8 for _ in chunks]
    meta = {"title": doc_id, "source": doc_id + ".txt",
            "file_type": "TXT", "owner": owner}
    store.add(doc_id, chunks, dummy, meta=meta)


def test_fts_cjk_substring():
    d, store = _new_store()
    try:
        _add_chunks(store, "红楼梦", [
            "北京市朝阳区2024年度报告提到营收增长15%",
            "上海自贸区试点方案聚焦跨境支付与API网关",
            "已记录上述两份报告的要点",
        ], owner="admin")

        hits = store.fts_search("北京市", top_k=5)
        check(len(hits) > 0 and any("北京市" in h["text"] for h in hits),
              f"中文子串「北京市」经 FTS5 命中 ({len(hits)} 条)")

        hits2 = store.fts_search("年度报告", top_k=5)
        check(len(hits2) > 0 and any("年度报告" in h["text"] for h in hits2),
              f"中文「年度报告」命中 ({len(hits2)} 条)")

        # score 越高越相关；命中行应排在最前（score>0 时排序有效）
        if hits:
            top = hits[0]
            check("score" in top and isinstance(top["score"], (int, float)),
                  f"命中结果含 score 字段（值={top.get('score')}）")
    finally:
        store._conn.close()
        os.remove(os.path.join(d, "kb.db"))
        os.rmdir(d)


def test_fts_short_query_like_fallback():
    d, store = _new_store()
    try:
        _add_chunks(store, "doc1", ["跨境支付与API网关的对接方案"], owner="admin")
        # <3 字符短查询：trigram 不友好，应走 LIKE 兜底
        hits = store.fts_search("API", top_k=5)
        check(len(hits) > 0 and any("API" in h["text"] for h in hits),
              f"短词「API」经 LIKE 兜底命中 ({len(hits)} 条)")

        hits2 = store.fts_search("报告", top_k=5)  # 2 字符
        # 无「报告」文档时应返回空，而非崩溃
        check(isinstance(hits2, list), "无命中短词查询返回空列表而非异常")
    finally:
        store._conn.close()
        os.remove(os.path.join(d, "kb.db"))
        os.rmdir(d)


def test_fts_owner_isolation():
    d, store = _new_store()
    try:
        _add_chunks(store, "alice_doc", ["alice的秘密：北京市规划"], owner="alice")
        _add_chunks(store, "bob_doc", ["bob的内容：上海市数据"], owner="bob")
        rb = store.fts_search("北京市", top_k=5, owner="bob")
        check(not any("alice" in h["text"] for h in rb),
              "owner 过滤生效：bob 检索不串入 alice 私有文档")
    finally:
        store._conn.close()
        os.remove(os.path.join(d, "kb.db"))
        os.rmdir(d)


# --------------------------- 3. _hybrid_fuse 融合 ---------------------------
def test_hybrid_fuse_degraded_exclusive():
    fts = [{"id": 1, "score": 0.9}, {"id": 2, "score": 0.3}]
    # 降级：向量为空 → 全文检索独占，直接返回 fts 列表
    out = KnowledgeBase._hybrid_fuse(fts, [], w_fts=0.4, w_vec=0.6)
    check(out == list(fts), "向量为空（降级）时 _hybrid_fuse 直接返回全文检索结果（独占）")

    # 正常：两者都有 → 按 id 去重加权。_hybrid_fuse 仅按融合分重排，score 仍保留
    # 原始值；真正归一化发生在 _finalize。故此处验证：① 全集命中 ② 融合冠军(id1)置顶
    # ③ 过 _finalize 后 score 归一化且降序。
    vec = [{"id": 1, "score": 0.8}, {"id": 3, "score": 0.5}]
    out2 = KnowledgeBase._hybrid_fuse(fts, vec, w_fts=0.4, w_vec=0.6)
    ids = [h["id"] for h in out2]
    check(set(ids) == {1, 2, 3}, f"融合后覆盖全部 id（{ids}）")
    check(ids[0] == 1, f"融合分最高项(id1)排在最前（顺序={ids}）")
    fin = KnowledgeBase._finalize(out2, top_k=5)
    fin_scores = [h["score"] for h in fin]
    check(fin_scores == sorted(fin_scores, reverse=True),
          f"_finalize 后 score 降序归一化（{fin_scores}）")


# --------------------------- 4. stats 透传 retrieval_mode ---------------------------
def test_rag_stats_retrieval_mode():
    # 未配置 embedding → retrieval_mode 应为 'fts'
    d = tempfile.mkdtemp()
    try:
        ec = EmbeddingConfig(backend="provider", embed_model="")
        vs = VectorStoreConfig(backend="sqlite", path=os.path.join(d, "kb.db"))
        rag = KnowledgeBase(ec, vs, data_dir=d)
        st = rag.stats()
        check(st.get("retrieval_mode") == "fts",
              f"未配置模型 → stats.retrieval_mode=='fts'（实际={st.get('retrieval_mode')}）")
        check(st.get("unconfigured") is True,
              "未配置模型 → stats.unconfigured==True")
        check(st.get("semantic_available") is False,
              "未配置模型 → stats.semantic_available==False")
        check(st.get("fts_available") is True,
              "FTS5 可用 → stats.fts_available==True")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # 已配模型 → retrieval_mode 应为 'hybrid'
    d2 = tempfile.mkdtemp()
    try:
        ec2 = EmbeddingConfig(backend="local", embed_model="")
        vs2 = VectorStoreConfig(backend="sqlite", path=os.path.join(d2, "kb.db"))
        rag2 = KnowledgeBase(ec2, vs2, data_dir=d2)
        st2 = rag2.stats()
        check(st2.get("retrieval_mode") == "hybrid",
              f"已配语义模型 → stats.retrieval_mode=='hybrid'（实际={st2.get('retrieval_mode')}）")
    finally:
        shutil.rmtree(d2, ignore_errors=True)


if __name__ == "__main__":
    print("== v1.0.62 Embedding 降级治理 / FTS 主干检索测试 ==")
    test_embedding_unconfigured()
    test_fts_cjk_substring()
    test_fts_short_query_like_fallback()
    test_fts_owner_isolation()
    test_hybrid_fuse_degraded_exclusive()
    test_rag_stats_retrieval_mode()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
