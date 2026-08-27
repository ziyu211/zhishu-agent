"""回归测试：会话记忆消息级 FTS5（trigram）中文子串检索（全量优化 T7）。

验证：
  1. turns_fts 已升级为 trigram 分词器（支持中文子串检索）；
  2. recall 对 ≥3 字符中文子串经 FTS5 命中；
  3. recall 对 <3 字符短词（如「报告」「API」）经 LIKE 兜底命中；
  4. 双路召回合并去重，结果正确且按 session 隔离。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.memory.sqlite_provider import MemoryStore

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


def _new_store():
    d = tempfile.mkdtemp()
    store = MemoryStore(os.path.join(d, "mem.db"))
    return d, store


def test_trigram_tokenizer():
    d, store = _new_store()
    try:
        row = store.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='turns_fts'"
        ).fetchone()
        check(row is not None and "trigram" in (row[0] or "").lower(),
              f"turns_fts 使用 trigram 分词器 (sql={row[0]})")
    finally:
        store.conn.close()
        os.remove(os.path.join(d, "mem.db"))
        os.rmdir(d)


def test_cjk_recall():
    d, store = _new_store()
    try:
        sid = "tester:conv1"
        store.append(sid, "user", "北京市朝阳区2024年度报告里提到营收增长15%")
        store.append(sid, "user", "上海自贸区试点方案聚焦跨境支付与API网关")
        store.append(sid, "assistant", "已记录上述两份报告的要点")

        r1 = store.recall(sid, "北京市", limit=5)
        check(any("北京市" in x for x in r1), f"3字符中文子串「北京市」召回成功 ({len(r1)}条)")

        r2 = store.recall(sid, "年度报告", limit=5)
        check(any("年度报告" in x for x in r2), f"「年度报告」召回成功 ({len(r2)}条)")

        r3 = store.recall(sid, "报告", limit=5)
        check(any("报告" in x for x in r3), f"2字符短词「报告」经 LIKE 兜底召回 ({len(r3)}条)")

        r4 = store.recall(sid, "API", limit=5)
        check(any("API" in x for x in r4), f"短词「API」经 LIKE 兜底召回 ({len(r4)}条)")

        r5 = store.recall(sid, "跨境支付", limit=5)
        check(any("跨境支付" in x for x in r5), f"中文「跨境支付」召回成功 ({len(r5)}条)")
    finally:
        store.conn.close()
        os.remove(os.path.join(d, "mem.db"))
        os.rmdir(d)


def test_recall_no_leak():
    d, store = _new_store()
    try:
        store.append("alice:conv", "user", "alice的秘密：北京市规划")
        store.append("bob:conv", "user", "bob的内容：上海市数据")
        rb = store.recall("bob:conv", "北京市", limit=5)
        check(not any("alice" in x for x in rb), "recall 按 session 隔离，不串号")
    finally:
        store.conn.close()
        os.remove(os.path.join(d, "mem.db"))
        os.rmdir(d)


if __name__ == "__main__":
    print("== 会话记忆 FTS5(trigram) 中文检索测试 ==")
    test_trigram_tokenizer()
    test_cjk_recall()
    test_recall_no_leak()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
