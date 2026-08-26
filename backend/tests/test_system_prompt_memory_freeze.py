"""系统提示「记忆冻结进稳定前缀」回归测试。

验证：
  * build_user_memory_prompt 能按 owner 读出 MEMORY.md / USER.md / SOUL.md；
  * build_agent_context_prompt(include_memory=False) 不再注入记忆（避免与稳定前缀重复）；
  * build_system_prompt 主管模式把长期记忆放进 stable 前缀（位于工具指引 volatile 之前），
    从而命中 prompt-cache、降低 token 成本与重建开销。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.modules import skills as skills_mod
from zhishu.core.agent import system_prompt as sp_mod


def _make_cfg(data_dir):
    return SimpleNamespace(
        system_prompt="BASE PERSONA",
        server=SimpleNamespace(data_dir=data_dir),
        agent=SimpleNamespace(skills_progressive=False),
    )


class TestMemoryFreeze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zhishu_sp_test_")
        owner_dir = os.path.join(self.tmp, "memory", "alice")
        os.makedirs(owner_dir, exist_ok=True)
        with open(os.path.join(owner_dir, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Alice 的长期记忆\n偏好用中文回复。")
        self.cfg = _make_cfg(self.tmp)

    def test_build_user_memory_prompt_includes_owner_memory(self):
        out = skills_mod.build_user_memory_prompt(self.cfg, "alice")
        self.assertIn("【长期记忆", out)
        self.assertIn("偏好用中文回复", out)

    def test_include_memory_false_excludes(self):
        with_mem = skills_mod.build_agent_context_prompt(self.cfg, owner="alice", include_memory=True)
        without = skills_mod.build_agent_context_prompt(self.cfg, owner="alice", include_memory=False)
        self.assertIn("【长期记忆", with_mem)
        self.assertNotIn("【长期记忆", without)

    def test_build_system_prompt_freezes_memory_in_stable_prefix(self):
        system, _ = sp_mod.build_system_prompt(self.cfg, owner="alice", kb=None, query=None)
        mem_idx = system.find("【长期记忆")
        tool_idx = system.find("【文档与图片解析指引】")
        self.assertGreaterEqual(mem_idx, 0, "系统提示应包含长期记忆")
        self.assertGreater(tool_idx, mem_idx,
                           "记忆应在 volatile 的工具指引之前（已冻结进稳定前缀）")


if __name__ == "__main__":
    unittest.main()
