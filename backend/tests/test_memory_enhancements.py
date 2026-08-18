"""智枢记忆增强（对标 Hermes MemoryManager）回归测试。

覆盖：
  * trivial 门控（中英文寒暄/占位）
  * 上下文围栏（sanitize_context / build_memory_context_block / StreamingContextScrubber）
  * normalize_tool_schema 防 DeepSeek 400 嵌套
  * MemoryManager后台非阻塞同步 + 抽取式入库（周期 / 会话结束 / 压缩前）
  * prefetch trivial 门控 + query rewrite 改写后召回

说明：需真实事件循环驱动后台 LLM 调用，因此测试自带一个后台事件循环线程；
      通过 monkeypatch create_memory_backend 注入无嵌入依赖的 FakeBackend，
      避免在 CI/本地缺少 embedding 后端时失败。建议在运行容器（含全部依赖）内执行：
          docker exec zsagent python -m unittest backend.tests.test_memory_enhancements -v
"""
from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import zhishu.core.memory.provider as prov
import zhishu.core.memory.manager as mgr


# ---------------------------------------------------------------------------
# 后台事件循环（驱动 run_coroutine_threadsafe 的 LLM 调用）
# ---------------------------------------------------------------------------
class _LoopThread(threading.Thread):
    def __init__(self, loop):
        super().__init__(daemon=True)
        self.loop = loop

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()


class FakeBackend:
    """无 embedding 依赖的假后端，记录 add/search。"""

    name = "fake"

    def __init__(self):
        self.store = []   # (owner, content, meta)
        self.queries = []

    def initialize(self):
        pass

    def add(self, owner, content, meta=None):
        self.store.append((owner, content, meta or {}))

    def search(self, owner, query, top_k=5):
        self.queries.append((owner, query))
        return [f"fake memory about {query}"]

    def stats(self, owner=None):
        return {"backend": "fake", "count": len(self.store), "owner": owner or "*"}

    def clear(self, owner=None):
        n = len(self.store)
        self.store = []
        return n


def _make_cfg():
    mem = SimpleNamespace(
        vector_enabled=True, vector_top_k=5, backend="builtin",
        query_rewrite_enabled=True, extraction_enabled=True,
        extraction_interval=1, extraction_model=None,
    )
    return SimpleNamespace(memory=mem)


class ProviderUnitTests(unittest.TestCase):
    def test_is_trivial_english(self):
        for t in ["hi", "Hi!", "thanks :)", "done???", "go ahead", ""]:
            self.assertTrue(prov.is_trivial_prompt(t), t)

    def test_is_trivial_chinese(self):
        for t in ["继续", "好的。", "收到！", "谢谢", "/reset", "  "]:
            self.assertTrue(prov.is_trivial_prompt(t), t)

    def test_is_not_trivial(self):
        for t in ["帮我查一下天气", "k8s 怎么配", "好久不见", "好的项目", "对吗"]:
            self.assertFalse(prov.is_trivial_prompt(t), t)

    def test_normalize_tool_schema(self):
        # 已二次包裹的坏 schema → None
        self.assertIsNone(mgr.normalize_tool_schema(
            {"type": "function", "function": {"type": "function", "function": {}}})
        )
        # 裸 function schema → 归一返回
        self.assertEqual(
            mgr.normalize_tool_schema(
                {"name": "x", "description": "d", "parameters": {}})["name"], "x")

    def test_sanitize_context(self):
        raw = "<memory-context>LEAK</memory-context> body"
        self.assertNotIn("LEAK", mgr.sanitize_context(raw))
        self.assertNotIn("<memory-context>", mgr.sanitize_context(raw))

    def test_build_memory_context_block(self):
        b = mgr.build_memory_context_block("hello")
        self.assertIn("<memory-context>", b)
        self.assertIn("NOT new user input", b)
        self.assertEqual(mgr.build_memory_context_block(""), "")

    def test_streaming_scrubber_splits_span(self):
        # 围栏必须是块级构造（标签独占一行、其后紧跟换行），与真实注入形态一致。
        s = mgr.StreamingContextScrubber()
        out = (s.feed("<memory-context>\n")
               + s.feed("leaked content\n")
               + s.feed("</memory-context>\n")
               + s.feed("visible text") + s.flush())
        self.assertNotIn("leaked", out)
        self.assertIn("visible text", out)


class ManagerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fb = FakeBackend()
        self._p = patch(
            "zhishu.core.memory.vector_provider.create_memory_backend",
            lambda cfg, dd: self.fb,
        )
        self._p.start()
        self.loop = asyncio.new_event_loop()
        self.loop_thread = _LoopThread(self.loop)
        self.loop_thread.start()
        asyncio.set_event_loop(self.loop)
        self.mm = mgr.MemoryManager(_make_cfg(), "data", None)

    def tearDown(self):
        self._p.stop()
        try:
            self.mm.shutdown_all()
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=2)

    def _set_llm(self, fn):
        async def _wrap(messages, model=None):
            return fn(messages, model)
        self.mm.set_llm(_wrap)

    def test_construct_only_one_external(self):
        self.assertTrue(self.mm.vector_enabled)
        self.assertIsNotNone(self.mm.get_provider("vector"))
        self.assertIsNotNone(self.mm.get_provider("builtin"))
        self.assertTrue(self.mm.has_tool("__none__") is False)

    def test_prefetch_trivial_gated(self):
        self.assertEqual(self.mm.prefetch_all("hi", owner="u"), "")
        self.assertEqual(self.mm.prefetch_all("好的。", owner="u"), "")

    def test_prefetch_with_query_rewrite(self):
        self._set_llm(lambda m, model=None: "What does the user prefer for deployment?")
        ctx = self.mm.prefetch_all("我喜欢用国产大模型做内网部署", owner="u1")
        self.assertIn("memory-context", ctx)
        self.assertIn("fake memory about", ctx)
        # 改写后的英文问句应作为 search 的 query 传入后端
        self.assertTrue(any("deployment" in q for _, q in self.fb.queries), self.fb.queries)

    def test_schedule_sync_raw_and_extraction(self):
        # extraction_interval=1 → 每轮立即抽取；内容 >12 字才会 raw 写入
        self._set_llm(lambda m, model=None: "- 用户偏好国产大模型\n- 用户在做内网部署")
        self.mm.schedule_sync(
            "用户说喜欢用国产大模型做内网部署", "好的，我帮你处理这个需求",
            owner="u2", session_id="s2")
        self.assertTrue(self.mm.flush_pending(timeout=8))
        extracted = [c for (o, c, meta) in self.fb.store if meta.get("source") == "extraction"]
        self.assertTrue(extracted, "extraction facts should be stored")
        raw = [c for (o, c, meta) in self.fb.store if meta.get("source") != "extraction"]
        self.assertTrue(raw, "raw turn should be stored")
        self.assertIn("国产大模型", extracted[0])

    def test_on_session_end_extraction(self):
        self._set_llm(lambda m, model=None: "- 项目代号 alpha")
        msgs = [{"role": "user", "content": "我在做 alpha 项目"},
                {"role": "assistant", "content": "好的"}]
        self.mm.on_session_end(msgs, owner="u3", session_id="s3")
        self.assertTrue(self.mm.flush_pending(timeout=8))
        facts = [c for (o, c, meta) in self.fb.store if meta.get("source") == "extraction"]
        self.assertTrue(any("alpha" in f for f in facts), facts)

    def test_on_pre_compress_returns_cached(self):
        self._set_llm(lambda m, model=None: "- 事实A")
        self.mm.schedule_sync("u说A", "好", owner="u4", session_id="s4")
        self.assertTrue(self.mm.flush_pending(timeout=8))
        pre = self.mm.on_pre_compress([{"role": "user", "content": "x"}],
                                      owner="u4", session_id="s4")
        self.assertIn("事实A", pre)

    def test_session_end_no_external_is_noop(self):
        # vector 关闭时不应抛错
        cfg = _make_cfg()
        cfg.memory.vector_enabled = False
        mm2 = mgr.MemoryManager(cfg, "data", None)
        try:
            mm2.on_session_end([{"role": "user", "content": "x"}], owner="u5", session_id="s5")
            self.assertFalse(mm2.vector_enabled)
        finally:
            mm2.shutdown_all()


if __name__ == "__main__":
    unittest.main(verbosity=2)
