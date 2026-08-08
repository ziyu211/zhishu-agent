"""回归测试：反空转熔断 + 工具并发（Task #395）。

直接驱动真实 Agent.run()，用假 LLM 与打桩的工具执行，复现并守住三类失控循环：

  1. 连续失败熔断：模型反复调用同一被白名单拦截的命令（terminal_run apt-get），
     连续 N 次失败后提前终止（不烧到 16 步）。
  2. 重复失败循环熔断：成功/失败交替的死循环（code_exec 装 Node 成功、terminal_run
     装 Node 被拦，交替往复）——连续失败计数被成功调用清零，故必须靠「签名累计」捕获。
  3. 工具步骤硬上限：即便无失败/循环，工具步骤数也受 max_tool_steps 硬约束。
  4. 并行叶子工具 + 正常收尾：同一响应返回多个非委派工具应并发执行且能正常结束，
     不得误触熔断。

测试方式：monkeypatch 掉会触达存储/KB 的辅助函数与 ToolRegistry.execute，仅验证循环
控制与事件流，不依赖任何外部服务。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import zhishu.core.agent.agent as agent_mod
from zhishu.core.tools.base import ToolContext
from zhishu.core.config import ZhishuConfig
from zhishu.core.agent.agent import Agent

PASS = 0
FAIL = []


def check(cond, name):
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}")


# ---------------------------------------------------------------------------
# 打桩：避免 run() 触达 KB / 存储 / 模型解析等重路径
# ---------------------------------------------------------------------------
agent_mod.build_system_prompt = lambda *a, **k: ("<sys>", False)
agent_mod.get_agent_meta = lambda name=None: {}
agent_mod.is_enabled = lambda name=None: False
agent_mod.resolve_tools = lambda *a, **k: []
agent_mod.build_agent_system_prompt = lambda *a, **k: ""

_EXEC_LOG = []  # 记录每次工具执行的 (name, args)


async def _fake_execute(name, args, ctx):
    _EXEC_LOG.append((name, args))
    if name == "terminal_run":
        return "[已拦截] 命令 apt-get 不在白名单内。允许的命令：cat、cp、curl…"
    if name == "code_exec":
        return "下载完成: 29235364 bytes"
    return "ok"


agent_mod.ToolRegistry.execute = _fake_execute


def _tool_resp(name, args):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "c1",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }],
            },
            "finish_reason": "tool_calls",
        }]
    }


def _text_resp(text):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": text, "tool_calls": None},
            "finish_reason": "stop",
        }]
    }


class _FakeLLM:
    """reply_fn(call_index) -> LLM 响应 dict。"""

    def __init__(self, reply_fn):
        self.reply_fn = reply_fn
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, max_tokens=None, tool_choice=None):
        idx = self.calls
        self.calls += 1
        return self.reply_fn(idx)


def _make_agent(cfg):
    sec = cfg.security
    ctx = ToolContext(kb=None, security=sec)
    return Agent(cfg, _FakeLLM(lambda n: _text_resp("")), ctx=ctx)


async def _collect(agent, reply_fn, **run_kw):
    agent.llm = _FakeLLM(reply_fn)
    _EXEC_LOG.clear()
    events = []
    async for ev in agent.run("请帮我装一下 Node.js", session="s1", **run_kw):
        events.append(ev)
    return events


def _breaker_fired(events):
    return any(e.get("type") == "done" and e.get("note") == "anti-runaway breaker triggered"
               for e in events)


def _tool_result_count(events):
    return sum(1 for e in events if e.get("type") == "tool_result")


# ---------------------------------------------------------------------------
# 用例 1：连续失败熔断（单工具反复被拦）
# ---------------------------------------------------------------------------
async def test_consecutive_failure():
    print("\n[1] 连续失败熔断：terminal_run apt-get 每次参数略异（签名不同，专测连续失败路径）")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 6
    cfg.agent.tool_cycle_break = 4
    cfg.agent.max_tool_steps = 64
    agent = _make_agent(cfg)

    def rf(n):
        # 参数带序号 → 每次签名不同，循环熔断不会误捕，纯验证连续失败累计
        return _tool_resp("terminal_run", {"command": f"apt-get install -y nodejs #{n}"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    check(fired, "熔断被触发（done.note == anti-runaway breaker triggered）")
    # 连续失败阈值=6，触发那一步不产出 tool_result，故已产出 5 个
    check(n_tool == 5, f"连续 6 次失败在第 6 步熔断（已产出工具步骤 {n_tool} == 5）")
    check(n_tool >= 1, "确实执行了若干工具调用")


# ---------------------------------------------------------------------------
# 用例 2：重复失败循环熔断（成功/失败交替）
# ---------------------------------------------------------------------------
async def test_alternating_cycle():
    print("\n[2] 重复失败循环熔断：code_exec 成功 / terminal_run 被拦 交替")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 6
    cfg.agent.tool_cycle_break = 4
    cfg.agent.max_tool_steps = 64
    agent = _make_agent(cfg)

    def rf(n):
        if n % 2 == 0:
            return _tool_resp("code_exec", {"code": "=== 通过 apt 安装 Node.js ===\n..."})
        return _tool_resp("terminal_run", {"command": "apt-get install -y nodejs"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    check(fired, "熔断被触发（交替循环也能被捕获）")
    check(n_tool <= 8, f"交替循环在签名累计达阈值后停止（实际 {n_tool} ≤ 8）")


# ---------------------------------------------------------------------------
# 用例 3：工具步骤硬上限
# ---------------------------------------------------------------------------
async def test_hard_cap():
    print("\n[3] 工具步骤硬上限：全成功但步骤过多也应终止")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 100
    cfg.agent.tool_cycle_break = 100
    cfg.agent.max_tool_steps = 5
    agent = _make_agent(cfg)

    def rf(n):
        return _tool_resp("ok_tool", {"x": n})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    check(fired, "硬上限触发的熔断被触发")
    check(n_tool <= cfg.agent.max_tool_steps + 1,
          f"工具步骤被硬上限约束（实际 {n_tool} ≤ {cfg.agent.max_tool_steps + 1}）")


# ---------------------------------------------------------------------------
# 用例 4：并行叶子工具 + 正常收尾（不得误触熔断）
# ---------------------------------------------------------------------------
async def test_parallel_normal():
    print("\n[4] 并行叶子工具 + 正常收尾：两个非委派工具应并发且正常结束")
    cfg = ZhishuConfig()
    agent = _make_agent(cfg)

    def rf(n):
        if n == 0:
            # 同一响应返回两个非委派叶子工具 → 应走并行分支
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "read_file",
                                                      "arguments": json.dumps({"path": "/a"})}},
                            {"id": "c2", "function": {"name": "search",
                                                      "arguments": json.dumps({"q": "x"})}},
                        ],
                    },
                    "finish_reason": "tool_calls",
                }]
            }
        return _text_resp("已全部完成，这是最终结论。")

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    final = [e for e in events if e.get("type") == "token"]
    final_text = " ".join(e.get("text", "") for e in final)
    check(not fired, "并行正常路径不误触熔断")
    check(n_tool == 2, f"两个叶子工具都被执行（实际 {n_tool}）")
    check(("read_file", {"path": "/a"}) in _EXEC_LOG, "read_file 确实被调用")
    check(("search", {"q": "x"}) in _EXEC_LOG, "search 确实被调用")
    check("最终结论" in final_text, "最终回答正常产出")


# ---------------------------------------------------------------------------
# 用例 5：确定性拦截早停（Task #399）—— 同一命令被安全策略拦截，第 2 次即终止且回显原因
# ---------------------------------------------------------------------------
async def test_deterministic_block_early_stop():
    print("\n[5] 确定性拦截早停：terminal_run 被白名单拦截，第 2 次即终止（不烧满 4 次）并回显原因")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 100    # 排除连续失败路径干扰
    cfg.agent.tool_cycle_break = 4
    cfg.agent.max_tool_steps = 64
    agent = _make_agent(cfg)

    def rf(n):
        # 每次参数完全相同 → 签名一致；被白名单拦截是确定性结果
        return _tool_resp("terminal_run", {"command": "apt-get install -y nodejs"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    stop_text = " ".join(e.get("text", "") for e in events if e.get("type") == "token")
    check(fired, "确定性拦截熔断被触发（done.note == anti-runaway breaker triggered）")
    # 第 1 次被拦产出 tool_result；第 2 次被拦前即判定终止，不再产 tool_result
    check(n_tool == 1, f"第 2 次被拦即终止（已产出工具步骤 {n_tool} == 1，而非烧满 4 次）")
    check("确定性" in stop_text, "停止提示明确指出拦截为确定性结果")
    check("安全策略" in stop_text and "原因" in stop_text, "停止提示回显拦截原因（白名单/开关/角色）")


# ---------------------------------------------------------------------------
# 用例 6：重复成功循环熔断（Task #399）—— 反复 read_file 同一文档（全成功，无失败信号）
#   这是用户实测踩到的「65 次 read_file 同一文档」失控：原熔断仅统计失败，故一路烧到
#   max_tool_steps(64) 才停；新增 tool_repeat_break 按完整签名(含参数)累计成功重复调用，
#   阈值 8，使失控在 8 次内即止，且不误伤「读取不同文件/不同行范围」。
# ---------------------------------------------------------------------------
async def test_success_repeat_loop():
    print("\n[6] 重复成功循环熔断：read_file 同一文档反复全成功调用，第 8 次即止（不止烧到 64）")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 100    # 排除连续失败/失败循环路径干扰
    cfg.agent.tool_cycle_break = 100
    cfg.agent.max_tool_steps = 64
    cfg.agent.tool_repeat_break = 8
    agent = _make_agent(cfg)

    def rf(n):
        # 每次参数完全相同 → 签名一致；结果恒为成功（"ok"），无任何失败信号
        return _tool_resp("read_file", {"path": "/data/分析文档.md"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    stop_text = " ".join(e.get("text", "") for e in events if e.get("type") == "token")
    check(fired, "重复成功循环熔断被触发（done.note == anti-runaway breaker triggered）")
    # 调用 1..7 各产出 1 个 tool_result；第 8 次签名累计达阈值即终止，不再产出 tool_result
    check(n_tool == 7, f"第 8 次重复即终止（已产出工具步骤 {n_tool} == 7，而非烧满 64）")
    check("重复读取" in stop_text or "重复" in stop_text, "停止提示点明「重复读取/调用」式停滞")
    check("停滞" in stop_text, "停止提示建议改用以增量处理替代整篇重读")

    # 反向用例：读取不同文件 / 不同行范围不应误触发重复熔断
    print("\n[6b] 反向：read_file 读取不同文件不应误触重复熔断")
    cfg2 = ZhishuConfig()
    cfg2.agent.tool_fail_break = 100
    cfg2.agent.tool_cycle_break = 100
    cfg2.agent.max_tool_steps = 12   # 缩小硬上限以便快速结束正常任务
    cfg2.agent.tool_repeat_break = 8
    agent2 = _make_agent(cfg2)

    def rf2(n):
        # 每次读不同文件 → 签名各异；最终以文本收尾
        if n < 10:
            return _tool_resp("read_file", {"path": f"/data/doc-{n}.md"})
        return _text_resp("已汇总各文档要点，这是结论。")

    events2 = await _collect(agent2, rf2)
    n_tool2 = _tool_result_count(events2)
    fired2 = _breaker_fired(events2)
    final2 = " ".join(e.get("text", "") for e in events2 if e.get("type") == "token")
    check(not fired2, "正常读取不同文件不误触重复熔断")
    check(n_tool2 == 10, f"10 个不同文件均被正常读取（实际 {n_tool2}）")
    check("结论" in final2, "正常多文件任务可产出结论")


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_consecutive_failure())
    loop.run_until_complete(test_alternating_cycle())
    loop.run_until_complete(test_hard_cap())
    loop.run_until_complete(test_parallel_normal())
    loop.run_until_complete(test_deterministic_block_early_stop())
    loop.run_until_complete(test_success_repeat_loop())
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
