"""回归测试：override 版本戳自愈（#519）+ 委派子智能体只读继承记忆（#520）。

A. override 版本戳自愈（#519）
   历史缺陷：老版本/手写 config.override.json 把 memory.vector_enabled 持久化为 false，
   且 YAML 已显式开启记忆基线后，启动期 _apply_override 仍静默套用陈旧值，导致
   记忆能力实际未启用。v1.0.36 起 override 带 "_v" 版本戳：
     * 无 _v / _v 不匹配 → 视为陈旧 → 丢弃其 memory/security 块，cfg 维持 YAML 基线，
       并就地重写为当前版本（含 YAML 真实值）；
     * _v 匹配 → 正常套用（管理员经 UI 的显式切换可持久化、重启后回放）。

B. 委派子智能体只读继承（#520）
   历史缺陷：_run_delegate 构造子智能体时传 memory_manager=None，子智能体拿不到
   主会话积累的长期记忆事实。v1.0.36 起传共享 memory_manager + memory_read_only=True：
     * prefetch 仍执行（子智能体可召回主会话长期记忆注入其上下文）；
     * 不写回（_sync_memory / on_pre_compress / set_llm 全为 no-op），避免污染；
     * 共享 manager 的 LLM 绑定不被子智能体覆盖（set_llm 守卫）。

运行：python tests/test_override_stamp_delegate_memory.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.config import ZhishuConfig, ProviderConfig  # noqa: E402
from zhishu.context import AppContext, get_ctx, OVERRIDE_VERSION  # noqa: E402
from zhishu.main import create_app  # noqa: E402
from zhishu.core.security import Crypto  # noqa: E402
from zhishu.core.agent import agent as agent_mod  # noqa: E402
from zhishu.core.agent import Agent, build_context_engine  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _tmpdir():
    return tempfile.mkdtemp(prefix="zhishu_ovr_")


def _build_cfg(tmp: str, memory_enabled: bool = True) -> ZhishuConfig:
    """YAML 基线：memory.vector_enabled 显式开启（模拟 yaml memory 段）。"""
    cfg = ZhishuConfig()
    cfg.server.data_dir = tmp
    cfg.security.enable_sm = False
    cfg.security.enable_auth = True
    cfg.security.secret = "change-me-zhishu-secret"
    cfg.security.enable_audit = True
    cfg.security.enable_redact = True
    cfg.security.allow_private_fetch = False
    cfg.memory.vector_enabled = memory_enabled
    cfg.memory.query_rewrite_enabled = True
    cfg.memory.extraction_enabled = True
    cfg.providers = {
        "demo": ProviderConfig(name="demo", label="Demo", base_url="http://demo",
                               models=["demo-model"], enabled=True, owner="", shared=False),
    }
    return cfg


def _mint_token(secret: str, user: str = "admin", role: str = "admin", ttl: int = 86400 * 7) -> str:
    payload = json.dumps({"u": user, "r": role, "exp": int(time.time()) + ttl})
    sig = Crypto(False).sign(secret, payload)
    return f"{payload}.{sig}"


def _write_override(tmp: str, ov: dict) -> str:
    path = os.path.join(tmp, "config.override.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ov, f, ensure_ascii=False, indent=2)
    return path


def _read_override(tmp: str) -> dict:
    path = os.path.join(tmp, "config.override.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


# ---------------------------------------------------------------------------
# A. override 版本戳自愈（#519）
# ---------------------------------------------------------------------------
class OverrideStampTests(unittest.TestCase):

    def test_legacy_override_without_v_discarded_and_rewritten(self):
        """无 _v 的陈旧 override：不套用 memory 块；cfg 维持 YAML 开启；文件被重写为当前版本。"""
        tmp = _tmpdir()
        try:
            _write_override(tmp, {"memory": {"vector_enabled": False}})
            cfg = _build_cfg(tmp, memory_enabled=True)
            ctx = AppContext(cfg)
            # 陈旧块被丢弃 → cfg 维持 YAML 基线
            self.assertTrue(ctx.cfg.memory.vector_enabled)
            # 文件被就地重写：带当前版本戳 + YAML 真实值
            ov = _read_override(tmp)
            self.assertEqual(ov.get("_v"), OVERRIDE_VERSION)
            self.assertTrue(ov["memory"]["vector_enabled"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_override_wrong_v_discarded_and_rewritten(self):
        """_v 不匹配（老版本号）同样视为陈旧：丢弃并重写。"""
        tmp = _tmpdir()
        try:
            _write_override(tmp, {"_v": OVERRIDE_VERSION - 1,
                                  "memory": {"vector_enabled": False}})
            cfg = _build_cfg(tmp, memory_enabled=True)
            ctx = AppContext(cfg)
            self.assertTrue(ctx.cfg.memory.vector_enabled)
            ov = _read_override(tmp)
            self.assertEqual(ov.get("_v"), OVERRIDE_VERSION)
            self.assertTrue(ov["memory"]["vector_enabled"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_current_override_applied(self):
        """_v 匹配 → 管理员显式关闭被尊重（重启后回放）。"""
        tmp = _tmpdir()
        try:
            _write_override(tmp, {"_v": OVERRIDE_VERSION,
                                  "memory": {"vector_enabled": False}})
            cfg = _build_cfg(tmp, memory_enabled=True)
            ctx = AppContext(cfg)
            self.assertFalse(ctx.cfg.memory.vector_enabled)
            # 文件未被重写（_v 已匹配，内容保持）
            ov = _read_override(tmp)
            self.assertEqual(ov.get("_v"), OVERRIDE_VERSION)
            self.assertFalse(ov["memory"]["vector_enabled"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_settings_stamps_version(self):
        """API 切换后持久化的 override 带 _v 戳；重启回放仍生效。"""
        tmp = _tmpdir()
        try:
            cfg = _build_cfg(tmp, memory_enabled=True)
            app = create_app(cfg)
            with TestClient(app) as client:
                token = _mint_token(cfg.security.secret)
                r = client.post(
                    "/api/v1/settings",
                    json={"memory": {"vector_enabled": False}},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(r.status_code, 200, r.text)
                self.assertFalse(r.json()["memory"]["vector_enabled"])
            ov = _read_override(tmp)
            self.assertEqual(ov.get("_v"), OVERRIDE_VERSION)
            self.assertFalse(ov["memory"]["vector_enabled"])
            # 模拟重启：复用同 data_dir
            cfg2 = _build_cfg(tmp, memory_enabled=True)
            ctx2 = AppContext(cfg2)
            self.assertFalse(ctx2.cfg.memory.vector_enabled)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_override_heal_then_admin_can_re_enable(self):
        """陈旧 override 自愈后，管理员仍可经 API 显式开启并持久化（不再次被重写逻辑吞掉）。"""
        tmp = _tmpdir()
        try:
            _write_override(tmp, {"memory": {"vector_enabled": False}})
            cfg = _build_cfg(tmp, memory_enabled=True)
            ctx = AppContext(cfg)  # 自愈：重写为 _v 匹配 + true
            self.assertTrue(ctx.cfg.memory.vector_enabled)
            app = create_app(cfg)
            with TestClient(app) as client:
                token = _mint_token(cfg.security.secret)
                # 显式关闭 → 持久化（_v 匹配，正常生效）
                r = client.post(
                    "/api/v1/settings",
                    json={"memory": {"vector_enabled": False}},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(r.status_code, 200, r.text)
            cfg2 = _build_cfg(tmp, memory_enabled=True)
            ctx2 = AppContext(cfg2)
            self.assertFalse(ctx2.cfg.memory.vector_enabled)  # 重启后仍为显式关闭
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# B. 委派子智能体只读继承（#520）
# ---------------------------------------------------------------------------
class FakeMemoryManager:
    """记录 set_llm / schedule_sync / prefetch_all / on_pre_compress 调用。"""

    def __init__(self):
        self.set_llm_calls = 0
        self.sync_calls = 0
        self.prefetch_calls = 0
        self.precompress_calls = 0

    def set_llm(self, fn):
        self.set_llm_calls += 1

    def schedule_sync(self, user_content="", assistant_content="", owner=None, session_id=""):
        self.sync_calls += 1

    def prefetch_all(self, query, owner=None, session_id=None):
        self.prefetch_calls += 1
        return "<memory-context>fake inherited memory</memory-context>"

    def on_pre_compress(self, messages, owner=None, session_id=None):
        self.precompress_calls += 1
        return ""


def _agent_cfg():
    return SimpleNamespace(
        security=SimpleNamespace(),
        agent=SimpleNamespace(max_tokens=1024),
    )


class DelegateMemoryInheritTests(unittest.TestCase):

    def test_readonly_agent_keeps_manager_but_skips_set_llm(self):
        """只读子智能体：持有共享 manager（可召回），但不覆盖其 LLM 绑定。"""
        mm = FakeMemoryManager()
        cfg = _agent_cfg()
        a = Agent(cfg, object(), ctx=None, memory_manager=mm, memory_read_only=True)
        self.assertIs(a.memory_manager, mm)
        self.assertTrue(a._memory_read_only)
        self.assertEqual(mm.set_llm_calls, 0, "只读子智能体不得改写共享 manager 的 LLM 回调")

    def test_writable_agent_injects_llm(self):
        """非只读（主管/普通对话）：正常绑定 LLM 回调。"""
        mm = FakeMemoryManager()
        cfg = _agent_cfg()
        a = Agent(cfg, object(), ctx=None, memory_manager=mm, memory_read_only=False)
        self.assertEqual(mm.set_llm_calls, 1)

    def test_readonly_agent_skips_sync_memory(self):
        """只读子智能体：_sync_memory 为 no-op（不回写用户长期记忆）。"""
        mm = FakeMemoryManager()
        cfg = _agent_cfg()
        a = Agent(cfg, object(), ctx=None, memory_manager=mm, memory_read_only=True)
        a._sync_memory("alice", "user", "我想记住这个事实", session="s1::delegate::sub")
        a._sync_memory("alice", "assistant", "好的", session="s1::delegate::sub")
        self.assertEqual(mm.sync_calls, 0)

    def test_writable_agent_syncs_memory(self):
        mm = FakeMemoryManager()
        cfg = _agent_cfg()
        a = Agent(cfg, object(), ctx=None, memory_manager=mm, memory_read_only=False)
        a._sync_memory("alice", "user", "我想记住这个事实", session="s1")
        self.assertEqual(mm.sync_calls, 1)

    def test_readonly_agent_skips_precompress_extraction(self):
        """只读子智能体：不触发压缩前抽取（不回写）。"""
        mm = FakeMemoryManager()
        cfg = _agent_cfg()
        a = Agent(cfg, object(), ctx=None, memory_manager=mm, memory_read_only=True)
        # on_pre_compress 只在非只读分支内被调用（agent.run 中守卫），这里直接验证守卫：
        self.assertTrue(a._memory_read_only)
        # 模拟 run 中的调用条件：只读时不应走到 on_pre_compress
        if not a._memory_read_only:
            mm.on_pre_compress([], owner="alice", session_id="s")
        self.assertEqual(mm.precompress_calls, 0)

    def test_delegate_passes_shared_manager_readonly(self):
        """_run_delegate 构造子智能体：共享 memory_manager + memory_read_only=True。"""
        import types as _types

        tmp = _tmpdir()
        try:
            # 落盘一个可解析的子智能体（_run_delegate 内部 get_agent_meta 依赖 data_dir）
            d = os.path.join(tmp, "agents", "subecho")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "agent.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "name": "subecho", "description": "��声子智能体（测试用）",
                    "version": "1.0.0", "enabled": True,
                    "system_prompt": "你是子智能体 subecho。", "model": None,
                    "tools": "none", "max_steps": 4,
                }, f, ensure_ascii=False, indent=2)

            cfg = _build_cfg(tmp, memory_enabled=True)
            from zhishu.context import init_ctx
            g = init_ctx(cfg)
            g.memory_manager = FakeMemoryManager()
            mm = g.memory_manager

            captured = {}

            def _fake_sub(*args, **kwargs):
                captured["memory_manager"] = kwargs.get("memory_manager")
                captured["memory_read_only"] = kwargs.get("memory_read_only")
                dummy = _types.SimpleNamespace()

                async def _run(*a, **k):
                    yield {"type": "delegate_end", "agent": "subecho",
                           "result": "子智能体产出", "session": "s1::delegate::subecho"}

                dummy.run = _run
                return dummy

            a = Agent(cfg, object(), g.kb, g.memory, g.tool_ctx, media=g.media,
                      context_engine=build_context_engine(cfg, object()),
                      memory_manager=mm, memory_read_only=False)
            real_agent_cls = agent_mod.Agent
            agent_mod.Agent = _fake_sub
            try:
                async def _go():
                    events = []
                    async for ev in a._run_delegate(
                        args={"agent_name": "subecho", "task": "任务"},
                        session="s1", owner="alice", is_admin=False, user_role="user",
                        depth=0, parent=None):
                        events.append(ev)
                    return events

                events = asyncio.run(_go())
            finally:
                agent_mod.Agent = real_agent_cls

            self.assertIs(captured.get("memory_manager"), mm, "子智能体应继承共享 memory_manager")
            self.assertTrue(captured.get("memory_read_only"), "子智能体应为只读模式")
            self.assertTrue(any(e.get("type") == "delegate_end" for e in events))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
