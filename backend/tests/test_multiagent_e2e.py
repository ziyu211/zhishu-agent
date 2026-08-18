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

import contextlib
import asyncio
import json
import os
import sqlite3
import shutil
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
# 临时数据目录：sqlite 连接在测试结束时未必已关闭，Windows 上会让目录删除抛
# WinError 32（另一个程序正在使用此文件），把「清理失败」误报成「测试失败」，
# 掩盖真实断言结果。清理是尽力而为，失败直接忽略。
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _tmpdir():
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)

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

    async def chat(self, messages, model=None, tools=None, max_tokens=None,
                   tool_choice=None):
        sys_c = (messages[0].get("content", "") if messages else "")
        if "SUBECHO_MARKER" in sys_c:
            # —— 子智能体分支 ——
            if FAKE_HANG:
                await asyncio.sleep(3600)  # 永不返回 → 触发委派超时熔断
            return _resp_text("收到任务：请用三句话介绍上海。")
        if "rewrite a user's latest message into" in sys_c:
            # —— 记忆 query_rewrite 分支（v1.0.35+）：Agent.run 的 prefetch 会先调用一次
            # LLM 做检索问句改写。必须返回纯文本（而非工具调用），否则会消耗主管首轮
            # 的 _calls 计数，导致主管首轮被误判为第二轮、直接返回纯文本而不委派。——
            return _resp_text("What prior user context would help answer the latest message?")
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
    with _tmpdir() as tmp:
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
    with _tmpdir() as tmp:
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
    with _tmpdir() as tmp:
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
# 测试 4（纯单测）：委派分类器保守判定（默认不委派，仅命中明确信号才委派）
# ---------------------------------------------------------------------------
def test_classifier_conservative():
    f = agent_mod._needs_supervisor_delegation
    # 路径 A：普通问题 / 能力咨询 / 简单创作 / 顺带提及领域词 → 不应委派
    for q in ["你能直接修改EXECL吗", "你会做什么", "什么是RAG", "今天天气怎么样",
              "帮我写一首关于春天的诗", "能读取PDF吗", "你好", "今天几号",
              "除了股票分析呢，你还能做什么", "帮我分析一下贵州茅台", "研究一下半导体行业",
              "做个量化回测", "对比一下工行和建行", "帮我分析一下这段代码的性能",
              "风险评估一般怎么做", "我们团队最近在讨论股票"]:
        assert f(q) is False, f"普通问题被误判为需委派: {q!r}"
    # 路径 B：显式建团 / 点名团队 / 显式多智能体协作 → 应委派
    for q in ["创建一个股票分析团队", "组建一个业务分析团队", "用Orchestrator调研新能源",
              "用股票分析团队分析贵州茅台", "业务分析团队帮我评估这个商业模式",
              "股票分析团队：分析贵州茅台", "多智能体协作分析这个项目", "多模型并行生成方案"]:
        assert f(q) is True, f"显式团队/多智能体请求未判为需委派: {q!r}"
    print("  [4] 分类器保守判定（默认不委派，仅显式点名团队/多智能体才委派）  ✓")


# ---------------------------------------------------------------------------
# 测试 5/6：路由治理 —— 路径 A 权威剥离委派工具，路径 B 保留并实际委派
# ---------------------------------------------------------------------------
def _write_coordinator(tmp: str) -> None:
    d = os.path.join(tmp, "agents", "orchestrator")
    os.makedirs(d, exist_ok=True)
    meta = {
        "name": "orchestrator",
        "description": "投资总监（协调者）",
        "version": "1.0.0",
        "enabled": True,
        "system_prompt": "你是协调者。",
        "model": None,
        "tools": ["delegate_to_agent", "web_search"],
        "max_steps": 8,
    }
    with open(os.path.join(d, "agent.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


class _RoutingFakeLLM(FakeLLM):
    def __init__(self, cfg, expect_delegate=None):
        super().__init__(cfg)
        self.expect_delegate = expect_delegate  # True/False：断言 delegate 工具可见性
        self.saw_delegate_tool = None

    async def chat(self, messages, model=None, tools=None, max_tokens=None,
                   tool_choice=None):
        sys_c = (messages[0].get("content", "") if messages else "")
        if "rewrite a user's latest message into" in sys_c:
            # 记忆 query_rewrite（tools=None）：返回纯文本，不污染 saw_delegate_tool 判定
            return _resp_text("What prior user context would help answer the latest message?")
        names = [t["function"]["name"] for t in (tools or [])]
        if self.saw_delegate_tool is None:
            self.saw_delegate_tool = "delegate_to_agent" in names
        if self.expect_delegate is not None:
            assert self.saw_delegate_tool == self.expect_delegate, (
                f"路由不符预期：路径{'B(应委派)' if self.expect_delegate else 'A(应直答)'} "
                f"下 delegate_to_agent 可见性={self.saw_delegate_tool}, tools={names}")
        if self.saw_delegate_tool:
            return _resp_tool("delegate_to_agent",
                              {"agent_name": "subecho", "task": "分析"})
        return _resp_text("（主管直接作答）普通问题无需多 Agent 协作。")


async def test_routing_path_a_simple_no_delegate():
    with _tmpdir() as tmp:
        g, cfg = _make_ctx(tmp)
        _write_coordinator(tmp)
        ToolRegistry.discover_builtin_tools()

        llm = _RoutingFakeLLM(g.cfg, expect_delegate=False)
        agent_mod.LLMClient = lambda c, m=None: llm
        sup = Agent(g.cfg, llm, g.kb, g.memory, g.tool_ctx, media=g.media,
                    context_engine=build_context_engine(g.cfg, llm),
                    memory_manager=g.memory_manager)
        events = await _collect(sup.run(
            "你能直接修改EXECL吗", session="ra", owner="admin"))
        types = [e.get("type") for e in events]
        assert "delegate_start" not in types, f"路径A普通问题不应委派, events={types}"
        assert llm.saw_delegate_tool is False, "路径A下 delegate_to_agent 不应出现在工具集"
        print("  [5] 路由A：普通问题权威剥离委派工具、直接作答（不浪费资源）  ✓")


async def test_routing_path_b_complex_delegate():
    with _tmpdir() as tmp:
        g, cfg = _make_ctx(tmp)
        _write_coordinator(tmp)
        _write_subagent(tmp)
        ToolRegistry.discover_builtin_tools()

        llm = _RoutingFakeLLM(g.cfg, expect_delegate=True)
        agent_mod.LLMClient = lambda c, m=None: llm
        sup = Agent(g.cfg, llm, g.kb, g.memory, g.tool_ctx, media=g.media,
                    context_engine=build_context_engine(g.cfg, llm),
                    memory_manager=g.memory_manager)
        events = await _collect(sup.run(
            "用Orchestrator分析贵州茅台", session="rb", owner="admin"))
        types = [e.get("type") for e in events]
        assert "delegate_start" in types, f"路径B复合任务应委派, events={types}"
        assert llm.saw_delegate_tool is True, "路径B下 delegate_to_agent 应可见"
        print("  [6] 路由B：复合任务保留委派工具并实际委派  ✓")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------
async def _run_all():
    await test_delegation_normal_and_rag()
    await test_delegation_timeout()
    await test_action_audit_tool_call()
    test_classifier_conservative()
    await test_routing_path_a_simple_no_delegate()
    await test_routing_path_b_complex_delegate()


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
