"""回归测试：反空转护栏（对标 Hermes IterationBudget，Task #395/#399/#415）。

直接驱动真实 Agent.run()，用假 LLM 与打桩的工具执行，守住核心不变式：

  · 运行在「迭代预算」(max_steps，默认 90，对齐 Hermes 父 Agent) 内自由推进；
    无论重复调用、失败循环还是确定性拦截，均**不再整体硬终止**任务。
  · 重复成功调用：转为「跳过冗余 + 停止重复提醒」，打破无意义重读/重列，运行继续。
  · 连续失败 / 确定性拦截：注入系统提醒引导改方案，运行继续（不再 kill）。
  · 唯一硬终止器是迭代预算 max_steps；耗尽前注入「收尾」提醒（grace call），
    任务以温和「已为你收尾」方式结束，而非被掐断、丢失成果。

测试方式：monkeypatch 掉会触达存储/KB 的辅助函数与 ToolRegistry.execute，仅验证循环
控制与事件流，不依赖任何外部服务。各用例通过 cfg.agent.max_steps 把预算调小，使运行
快速抵达预算上限、验证「不早杀 + 预算兜底 + 收尾」行为。
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
    # 旧语义：done.note == "anti-runaway breaker triggered"。新设计下该 note 不再出现——
    # 用它断言「没有早期硬熔断把任务掐断」。
    return any(e.get("type") == "done" and e.get("note") == "anti-runaway breaker triggered"
               for e in events)


def _ended_by_budget(events):
    return any(e.get("type") == "done"
               and str(e.get("note", "")).startswith("iteration budget reached")
               for e in events)


def _tool_result_count(events):
    return sum(1 for e in events if e.get("type") == "tool_result")


# ---------------------------------------------------------------------------
# 用例 1：连续失败不再硬终止——运行持续推进至迭代预算上限，而非第 6 步被杀
# ---------------------------------------------------------------------------
async def test_consecutive_failure_continues():
    print("\n[1] 连续失败不再硬终止：terminal_run apt-get 每次参数略异（签名不同）")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 6
    cfg.agent.tool_cycle_break = 4
    cfg.agent.max_steps = 12          # 把迭代预算调小，快速抵达上限
    agent = _make_agent(cfg)

    def rf(n):
        # 参数带序号 → 每次签名不同，循环熔断不会误捕，纯验证连续失败累计
        return _tool_resp("terminal_run", {"command": f"apt-get install -y nodejs #{n}"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    check(not fired, "无早期硬熔断（done.note 不再是 anti-runaway breaker triggered）")
    # 连续失败不再在第 6 步杀任务，而是继续推进到迭代预算（12 步，每步 1 工具）
    check(n_tool == 12, f"运行持续推进至迭代预算上限（实际 {n_tool} == 12，而非 6 步被杀）")
    check(_ended_by_budget(events), "由迭代预算（max_steps）兜底结束，而非被掐断")


# ---------------------------------------------------------------------------
# 用例 2：重复失败循环（成功/失败交替）不再硬终止
# ---------------------------------------------------------------------------
async def test_alternating_cycle_continues():
    print("\n[2] 重复失败循环不再硬终止：code_exec 成功 / terminal_run 被拦 交替")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 6
    cfg.agent.tool_cycle_break = 4
    cfg.agent.max_steps = 12
    agent = _make_agent(cfg)

    def rf(n):
        if n % 2 == 0:
            return _tool_resp("code_exec", {"code": "=== 通过 apt 安装 Node.js ===\n..."})
        return _tool_resp("terminal_run", {"command": "apt-get install -y nodejs"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    check(not fired, "交替循环不再被早期硬掐断")
    check(n_tool == 12, f"交替循环持续推进至预算上限（实际 {n_tool} == 12）")
    check(("code_exec", {"code": "=== 通过 apt 安装 Node.js ===\n..."}) in _EXEC_LOG,
          "code_exec 仍被执行")
    check(("terminal_run", {"command": "apt-get install -y nodejs"}) in _EXEC_LOG,
          "terminal_run 仍被执行（被拦但任务继续）")
    check(_ended_by_budget(events), "由迭代预算兜底结束")


# ---------------------------------------------------------------------------
# 用例 3：迭代预算是唯一的硬终止器（替代原「工具步骤硬上限」）
# ---------------------------------------------------------------------------
async def test_iteration_budget_is_final_stop():
    print("\n[3] 迭代预算为唯一硬终止器：全成功调用也应在预算上限处温和收尾")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 100
    cfg.agent.tool_cycle_break = 100
    cfg.agent.max_steps = 5
    agent = _make_agent(cfg)

    def rf(n):
        return _tool_resp("ok_tool", {"x": n})   # 每次签名不同 → 不触发重复跳过

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    check(not fired, "无早期硬熔断")
    check(n_tool == 5, f"运行在迭代预算（5）处停止（实际 {n_tool} == 5）")
    check(_ended_by_budget(events), "由迭代预算兜底结束")


# ---------------------------------------------------------------------------
# 用例 4：并行叶子工具 + 正常收尾（不得误触熔断，任务顺利完成）
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
# 用例 5：确定性拦截不再硬终止——被拦命令继续运行到预算，且不误导用户调安全配置
# ---------------------------------------------------------------------------
async def test_deterministic_block_continues():
    print("\n[5] 确定性拦截不再硬终止：terminal_run 被白名单拦截，运行继续至预算而非第 2 步被杀")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 100
    cfg.agent.tool_cycle_break = 4
    cfg.agent.max_steps = 12
    agent = _make_agent(cfg)

    def rf(n):
        # 每次参数完全相同 → 签名一致；被白名单拦截是确定性结果
        return _tool_resp("terminal_run", {"command": "apt-get install -y nodejs"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    tool_texts = [e.get("result", "") for e in events if e.get("type") == "tool_result"]
    check(not fired, "确定性拦截不再在第 2 步硬终止任务")
    check(n_tool == 12, f"被拦命令仍继续运行至预算上限（实际 {n_tool} == 12，而非 1 步被杀）")
    check(all(t.startswith("[已拦截]") for t in tool_texts),
          "每次拦截结果仍透传给模型（模型可知己被拦，自行改方案）")
    check(_ended_by_budget(events), "由迭代预算兜底结束")


# ---------------------------------------------------------------------------
# 用例 6：重复成功循环不再整体终止——转为「跳过+提醒」并继续，直至预算收尾
# ---------------------------------------------------------------------------
async def test_success_repeat_loop():
    print("\n[6] 重复成功循环不再整体终止：read_file 同一文档反复全成功，第 8 次起转「跳过+提醒」并继续")
    cfg = ZhishuConfig()
    cfg.agent.tool_fail_break = 100
    cfg.agent.tool_cycle_break = 100
    cfg.agent.max_steps = 12           # 预算内即可验证「不早杀 + 预算兜底」
    cfg.agent.tool_repeat_break = 8
    agent = _make_agent(cfg)

    def rf(n):
        # 每次参数完全相同 → 签名一致；结果恒为成功（"ok"），无任何失败信号
        return _tool_resp("read_file", {"path": "/data/分析文档.md"})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    fired = _breaker_fired(events)
    tool_texts = [e.get("result", "") for e in events if e.get("type") == "tool_result"]
    # 前 7 次为正常执行结果；第 8 次起被替换为「跳过重复执行」提示，循环被打破而不终止整轮
    # 注：read_file 单读有独立硬熔断（_READ_FILE_HARD_LIMIT=6），会在通用重复循环阈值前介入，
    # 故前 6 次正常执行、第 7 次起转为熔断/强指令，而非整轮被掐断。
    check(tool_texts[:6] == ["ok"] * 6, "前 6 次重复调用（read_file 硬熔断阈值之前）仍正常执行")
    check(any("系统·熔断" in t for t in tool_texts), "达 read_file 硬熔断阈值后转为熔断/强指令（而非整轮被掐断）")
    # 关键回归：纯成功重复不再把整个任务掐断——运行继续，直至迭代预算兜底收尾
    check(n_tool == 12, f"运行未被重复熔断提前掐断（产出 {n_tool} 个工具步骤，直至预算兜底，而非 7）")
    check(not fired, "最终由迭代预算收尾，而非「anti-runaway breaker triggered」早杀")
    check(_ended_by_budget(events), "结束原因为迭代预算（温和收尾，而非任务被截断）")

    # 反向用例：读取不同文件 / 不同行范围不应误触发重复熔断，且能正常产出结论
    print("\n[6b] 反向：read_file 读取不同文件不应误触重复熔断")
    cfg2 = ZhishuConfig()
    cfg2.agent.tool_fail_break = 100
    cfg2.agent.tool_cycle_break = 100
    cfg2.agent.max_steps = 90        # 正常多文件任务应自然结束，不触碰预算
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


# ---------------------------------------------------------------------------
# 用例 7：grace 收尾——预算耗尽时注入收尾提醒并以「已为你收尾」温和结束（不再「已自动终止」）
# ---------------------------------------------------------------------------
async def test_grace_finalize():
    print("\n[7] 迭代预算耗尽时温和收尾（grace call）：不再「已自动终止」，而是「已为你收尾」")
    cfg = ZhishuConfig()
    cfg.agent.max_steps = 6
    agent = _make_agent(cfg)

    def rf(n):
        # 每次签名不同、全成功，纯跑满预算以触发收尾路径
        return _tool_resp("ok_tool", {"x": n})

    events = await _collect(agent, rf)
    n_tool = _tool_result_count(events)
    final = [e for e in events if e.get("type") == "token"]
    final_text = " ".join(e.get("text", "") for e in final)
    check(n_tool == 6, f"运行在预算（6）处停止（实际 {n_tool}）")
    check("已为你收尾" in final_text, "预算耗尽时以「已为你收尾」温和结束（非「已自动终止」）")
    check("已自动终止" not in final_text, "措辞不再含「已自动终止」")
    check(_ended_by_budget(events), "结束 note 为 iteration budget reached（温和收尾）")


# ---------------------------------------------------------------------------
# 用例 8（P0-C）：硬熔断消息携带中文「自愈提示」，引导模型换思路而非原样重试
# ---------------------------------------------------------------------------
async def test_breaker_message_has_self_heal_hint():
    print("\n[8] P0-C：code_exec / read_file 硬熔断消息注入中文自愈提示")
    cfg = ZhishuConfig()
    cfg.agent.max_steps = 60
    agent = _make_agent(cfg)

    # code_exec 走 REPL 式增量循环：连续 11 次调用（>_CODE_EXEC_HARD_LIMIT=8），
    # 交替放行/熔断 —— 任意一次被熔断返回的消息都应含 [自愈提示] 且指向 code_exec 的修复路径。
    def rf(n):
        if n < 11:
            return _tool_resp("code_exec", {"code": f"print({n})  # 逐步调试"})
        return _text_resp("已整合为完整脚本并产出交付物。")

    events = await _collect(agent, rf)
    # 仅取「本次不执行」的熔断消息（区别于「最后一次机会」放行强指令）
    breaker_texts = [e.get("result", "") for e in events
                     if e.get("type") == "tool_result"
                     and "系统·熔断" in (e.get("result") or "")
                     and "本次不执行" in (e.get("result") or "")]
    check(len(breaker_texts) > 0, "确实触发了 code_exec 硬熔断消息（本次不执行）")
    check(all("自愈提示" in t for t in breaker_texts),
          "所有 code_exec 熔断消息均含「自愈提示」")
    check(all("generate_excel" in t for t in breaker_texts),
          "code_exec 自愈提示指向整合脚本 + generate_excel 出交付物")

    # read_file 走逐文件循环：连续 9 次单读（>_READ_FILE_HARD_LIMIT=6），
    # 被熔断消息应含 [自愈提示] 且指向 paths 批量读取。
    cfg2 = ZhishuConfig()
    cfg2.agent.max_steps = 60
    agent2 = _make_agent(cfg2)

    def rf2(n):
        if n < 9:
            return _tool_resp("read_file", {"path": f"/data/doc-{n}.md"})
        return _text_resp("已改用 paths 批量读取并汇总。")

    events2 = await _collect(agent2, rf2)
    rf_breaker = [e.get("result", "") for e in events2
                  if e.get("type") == "tool_result"
                  and "系统·熔断" in (e.get("result") or "")
                  and "本次不读取" in (e.get("result") or "")]
    check(len(rf_breaker) > 0, "确实触发了 read_file 硬熔断消息（本次不读取）")
    check(all("自愈提示" in t for t in rf_breaker),
          "所有 read_file 熔断消息均含「自愈提示」")
    check(all("paths" in t for t in rf_breaker),
          "read_file 自愈提示指向 paths 批量读取")


# ---------------------------------------------------------------------------
# 用例 9（P0-C）：幂等无进展检测——同一工具连续返回相同结果（参数可不同）注入自愈引导
# ---------------------------------------------------------------------------
async def test_idempotent_no_progress_nudge():
    print("\n[9] P0-C：幂等无进展检测——连续相同结果（如反复 pwd）触发自愈引导，不终止")
    # 自愈引导注入到内部 messages（不随事件流透出），故用调用记录器验证确实被调用。
    calls = []
    _orig = agent_mod._self_heal_hint

    def _rec(name):
        calls.append(name)
        return _orig(name)
    agent_mod._self_heal_hint = _rec
    try:
        cfg = ZhishuConfig()
        cfg.agent.tool_fail_break = 100
        cfg.agent.tool_cycle_break = 100
        cfg.agent.max_steps = 12
        agent = _make_agent(cfg)

        def rf(n):
            # 参数每次略异（模拟反复 pwd / ls 当前目录），但结果恒定相同 → 成功但无进展
            return _tool_resp("ok_tool", {"x": f"pwd #{n}"})

        events = await _collect(agent, rf)
        n_tool = _tool_result_count(events)
        # 兜底：仍由迭代预算收尾，不早杀
        check(n_tool == 12, f"幂等无进展循环持续推进至预算（实际 {n_tool} == 12，未早杀）")
        check(_ended_by_budget(events), "由迭代预算兜底结束")
        # 关键新增：连续相同结果后，_self_heal_hint("ok_tool") 被调用（注入自愈引导）
        check("ok_tool" in calls,
              "连续相同结果触发「成功但无进展」自愈引导（非仅失败才提醒）")
    finally:
        agent_mod._self_heal_hint = _orig


# ---------------------------------------------------------------------------
# 用例 10（P0-C）：失败软提醒也带自愈提示（terminal_run 被拦场景）
# ---------------------------------------------------------------------------
async def test_failure_nudge_has_self_heal_hint():
    print("\n[10] P0-C：连续失败软提醒携带自愈提示（terminal_run 被白名单拦截）")
    calls = []
    _orig = agent_mod._self_heal_hint

    def _rec(name):
        calls.append(name)
        return _orig(name)
    agent_mod._self_heal_hint = _rec
    try:
        cfg = ZhishuConfig()
        cfg.agent.tool_fail_break = 3
        cfg.agent.tool_cycle_break = 4
        cfg.agent.max_steps = 12
        agent = _make_agent(cfg)

        def rf(n):
            return _tool_resp("terminal_run", {"command": f"apt-get install -y nodejs #{n}"})

        events = await _collect(agent, rf)
        check("terminal_run" in calls,
              "连续失败软提醒调用 _self_heal_hint('terminal_run') 注入自愈提示")
    finally:
        agent_mod._self_heal_hint = _orig


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_consecutive_failure_continues())
    loop.run_until_complete(test_alternating_cycle_continues())
    loop.run_until_complete(test_iteration_budget_is_final_stop())
    loop.run_until_complete(test_parallel_normal())
    loop.run_until_complete(test_deterministic_block_continues())
    loop.run_until_complete(test_success_repeat_loop())
    loop.run_until_complete(test_grace_finalize())
    loop.run_until_complete(test_breaker_message_has_self_heal_hint())
    loop.run_until_complete(test_idempotent_no_progress_nudge())
    loop.run_until_complete(test_failure_nudge_has_self_heal_hint())
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
