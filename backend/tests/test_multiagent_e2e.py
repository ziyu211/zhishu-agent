"""智枢多 Agent 协作 —— 端到端集成测试（不依赖任何外部 LLM / 网络）。

覆盖企业级优化的关键行为，确定性复现（用 FakeLLM 取代真实模型）：
  * 委派正常：主管 delegate_to_agent → 子智能体在独立 scratch 会话产出 → delegate_end 回传
  * 委派超时熔断：子智能体卡死时按 delegate_timeout 中止，不挂死
  * 子智能体继承用户 RAG / 长期记忆（按 owner 隔离），且不注入全局技能指令
  * 动作级审计：委派事件 + 工具调用（tool_call）均落审计库（验证 ToolRegistry.execute 的 cls._audit_tool 修复）

运行方式（二选一）：
  * 独立运行：  python tests/test_multiagent_e2e.py        （无需 pytest）
  * pytest：    pytest tests/test_multiagent_e2e.py -o asyncio_mode=auto
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile

# 让脚本无论从哪个目录启动都能 import 到 zhishu 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.config import ZhishuConfig
from zhishu.context import init_ctx
from zhishu.core.agent import agent as agent_mod
from zhishu.core.agent import Agent, build_context_engine
from zhishu.core.tools.registry import ToolRegistry
from zhishu.core.tools.base import Tool, ToolContext


# ---------------------------------------------------------------------------
# FakeLLM：取代真实模型，按 system prompt 中的标记区分主管 / 子智能体
# ---------------------------------------------------------------------------
FAKE_HANG = False  # 全局开关：让子智能体卡死以触发超时熔断


def _resp_text(text: str) -> dict:
    return {
        "choices": [{
            "message": {"role": "assistant", "content": text, "tool_calls": None},
            "finish_reason": "stop",
        }]
    }


def _resp_tool(name: str, args: dict) -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }],
            },
            "finish_reason": "tool_calls",
        }]
    }


class FakeLLM:
    def __init__(self, cfg, api_mode=None):
        self.cfg = cfg
        self.api_mode = api_mode
        self._calls = 0

    async def chat(self, messages, model=None, tools=None, max_tokens=None):
        sys_c = (messages[0].get("content", "") if messages else "")
        if "SUBECHO_MARKER" in sys_c:
            # —— 子智能体分支 ——
            if FAKE_HANG:
                await asyncio.sleep(3600)  # 永不返回 → 触发委派超时熔断
            return _resp_text("收到任务：请用三句话介绍上海。")
        # —— 主管分支：首轮委派，次轮收尾 ——
        self._calls += 1
        if self._calls == 1:
            return _resp_tool("delegate_to_agent",
                              {"agent_name": "subecho", "task": "用三句话介绍上海"})
        return _resp_text("已委派子智能体完成介绍。")


# ---------------------------------------------------------------------------
# 测试上下文搭建
# ---------------------------------------------------------------------------
def _make_ctx(tmp: str, delegate_timeout: float = 60.0):
    cfg = ZhishuConfig()
    cfg.server.data_dir = tmp
    cfg.security.enable_sm = False          # 用 SHA256，避免依赖 gmssl
    cfg.security.enable_audit = True        # 开启审计，便于校验动作级留痕
    cfg.agent.delegate_timeout = delegate_timeout
    g = init_ctx(cfg)
    # 打桩：用 FakeLLM 取代真实模型
    agent_mod.LLMClient = FakeLLM
    return g, cfg


def _write_subagent(tmp: str) -> None:
    d = os.path.join(tmp, "agents", "subecho")
    os.makedirs(d, exist_ok=True)
    meta = {
        "name": "subecho",
        "description": "回声子智能体（测试用）",
        "version": "1.0.0",
        "enabled": True,
        "system_prompt": "你是子智能体 subecho，回声专家。SUBECHO_MARKER",
        "model": None,
        "tools": "none",
        "max_steps": 4,
    }
    with open(os.path.join(d, "agent.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _audit_rows(db_path: str):
    try:
        c = sqlite3.connect(db_path)
        rows = c.execute(
            "SELECT ts,user,action,detail FROM audit ORDER BY rowid").fetchall()
        c.close()
        return rows
    except Exception:
        return []


async def _collect(run_gen):
    """把一个 async generator 跑完，收集事件列表。"""
    events = []
    async for ev in run_gen:
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 测试 1：委派正常 + 子智能体继承 RAG（按 owner）+ 委派审计落库
# ---------------------------------------------------------------------------
async def test_delegation_normal_and_rag():
    with tempfile.TemporaryDirectory() as tmp:
        g, cfg = _make_ctx(tmp)
        _write_subagent(tmp)

        # 打桩：记录 build_system_prompt 调用（验证子智能体按 owner 继承 RAG/记忆）
        calls = []
        real_bsp = agent_mod.build_system_prompt

        def spy_bsp(*a, **k):
            calls.append((k.get("agent_name"), k.get("owner")))
            return real_bsp(*a, **k)

        agent_mod.build_system_prompt = spy_bsp

        sup = Agent(g.cfg, FakeLLM(g.cfg), g.kb, g.memory, g.tool_ctx,
                    media=g.media,
                    context_engine=build_context_engine(g.cfg, FakeLLM(g.cfg)),
                    memory_manager=g.memory_manager)
        events = await _collect(sup.run(
            "请让 subecho 用三句话介绍上海", session="s1", owner="admin"))

        # 断言：委派事件链完整
        types = [e.get("type") for e in events]
        assert "delegate_start" in types, f"缺少 delegate_start，事件: {types}"
        assert "delegate_end" in types, f"缺少 delegate_end，事件: {types}"
        # 子智能体产出被聚合进主消息
        end = next(e for e in events if e.get("type") == "delegate_end")
        assert "上海" in (end.get("result") or ""), f"委派结果未含子智能体产出: {end}"

        # 断言：子智能体按 owner=admin 走了独立系统提示（继承 RAG/记忆）
        assert ("subecho", "admin") in calls, \
            f"子智能体未以 owner=admin 构建系统提示，calls={calls}"

        # 断言：审计库出现委派条目
        rows = _audit_rows(os.path.join(tmp, "zhishu_audit.db"))
        assert any(r[2] == "delegate" and "subecho" in (r[3] or "") for r in rows), \
            f"审计库缺少 delegate 记录: {rows}"

        # 清理打桩
        agent_mod.build_system_prompt = real_bsp
        print("  [1] 委派正常 + 子智能体继承RAG(owner=admin) + 委派审计  ✓")


# ---------------------------------------------------------------------------
# 测试 2：委派超时熔断（子智能体卡死不挂死）
# ---------------------------------------------------------------------------
async def test_delegation_timeout():
    global FAKE_HANG
    with tempfile.TemporaryDirectory() as tmp:
        g, cfg = _make_ctx(tmp, delegate_timeout=0.5)
        _write_subagent(tmp)

        FAKE_HANG = True  # 让子智能体卡死
        try:
            sup = Agent(g.cfg, FakeLLM(g.cfg), g.kb, g.memory, g.tool_ctx,
                        media=g.media,
                        context_engine=build_context_engine(g.cfg, FakeLLM(g.cfg)),
                        memory_manager=g.memory_manager)
            t0 = asyncio.get_event_loop().time()
            events = await _collect(sup.run(
                "请让 subecho 卡死", session="s2", owner="admin"))
            elapsed = asyncio.get_event_loop().time() - t0
        finally:
            FAKE_HANG = False

        assert elapsed < 5.0, f"委派未在超时内中止，耗时 {elapsed:.1f}s"
        end = next((e for e in events if e.get("type") == "delegate_end"), None)
        assert end is not None, f"缺少 delegate_end，events={events}"
        assert "委派超时" in (end.get("result") or ""), \
            f"超时未触发中止文案: {end}"
        print("  [2] 委派超时熔断（0.5s 中止，不挂死）  ✓")


# ---------------------------------------------------------------------------
# 测试 3：动作级审计 —— 工具调用经 ToolRegistry.execute 落 tool_call（验证 self→cls 修复）
# ---------------------------------------------------------------------------
async def test_action_audit_tool_call():
    with tempfile.TemporaryDirectory() as tmp:
        g, cfg = _make_ctx(tmp)

        captured = {}

        async def fake_tool(args, ctx):
            captured["args"] = args
            return f"echo:{args.get('msg')}"

        ToolRegistry.register(Tool(
            name="e2e_fake_echo",
            description="单元测试用回声工具",
            parameters={"type": "object",
                        "properties": {"msg": {"type": "string"}}},
            handler=fake_tool,
            toolset="builtin",
        ))
        ctx = ToolContext(user="admin", session="s3", security=cfg.security)
        res = await ToolRegistry.execute("e2e_fake_echo", {"msg": "hi"}, ctx)
        assert res == "echo:hi", f"工具未正确执行: {res}"

        rows = _audit_rows(os.path.join(tmp, "zhishu_audit.db"))
        assert any(r[2] == "tool_call" and "e2e_fake_echo" in (r[3] or "")
                   for r in rows), f"审计库缺少 tool_call 记录: {rows}"
        print("  [3] 动作级审计 tool_call 落库（execute 的 cls._audit_tool）  ✓")
        ToolRegistry.unregister("e2e_fake_echo")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------
async def _run_all():
    await test_delegation_normal_and_rag()
    await test_delegation_timeout()
    await test_action_audit_tool_call()


def main():
    try:
        asyncio.run(_run_all())
    except AssertionError as e:
        print("FAILED:", e)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print("ERROR:", repr(e))
        sys.exit(2)
    print("\nALL E2E TESTS PASSED")


if __name__ == "__main__":
    main()
