"""P1-D 打断信号监听（对标 Hermes reverse-signal 立即终止）回归测试。

覆盖：
  1. detect_reverse_signal 对常见中国口语化打断词的识别；
  2. 防御误杀：长消息、带任务语义的长句、前缀不是停止短语的指令均不触发；
  3. Agent.run() 入口在检测到打断信号时直接返回，不启动工具循环。
"""
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import zhishu.core.agent.agent as agent_mod
from zhishu.core.config import ZhishuConfig
from zhishu.core.agent.agent import Agent, detect_reverse_signal
from zhishu.core.tools.base import ToolContext

PASS = 0
FAIL = []


def check(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(msg)
        print(f"  [FAIL] {msg}")


# ---------------------------------------------------------------------------
# 打桩：避免 run() 触达 KB / 存储 / 模型解析等重路径
# ---------------------------------------------------------------------------
agent_mod.build_system_prompt = lambda *a, **k: ("<sys>", False)
agent_mod.get_agent_meta = lambda name=None: {}
agent_mod.is_enabled = lambda name=None: False
agent_mod.resolve_tools = lambda *a, **k: []
agent_mod.build_agent_system_prompt = lambda *a, **k: ""
agent_mod.ToolRegistry.execute = lambda name, args, ctx: "ok"


# ---------------------------------------------------------------------------
# 用例 1：短语识别 + 误杀防御
# ---------------------------------------------------------------------------
def test_phrases():
    print("\n[1] detect_reverse_signal 短语识别 + 误杀防御")
    true_cases = [
        "停", "停一下", "停下", "停止", "别继续", "算了", "算了吧",
        "不用了", "取消", "中止", "放弃", "等等", "等一下", "换一个",
        "换个方案", "重新来", "重来", "先别", "暂停", "别管了",
    ]
    false_cases = [
        "帮我停掉后台服务",         # 前缀 + 实质指令 -> 不应触发
        "如何停止定时任务",          # 疑问句 -> 不应触发
        "请帮我取消上一次的调用",    # 长句前缀 -> 不应触发
        "今天天气真好，停",        # 停止短语不在开头 -> 不应触发
        "这是一个很长的请求，前面" * 20,  # 超长消息 -> 不应触发（>60字）
        "继续刚才的任务",          # 以「继续」开头 -> 不应触发
        "",                        # 空消息 -> 不应触发
        "请继续",                  # 正向指令 -> 不应触发
    ]
    for phrase in true_cases:
        check(detect_reverse_signal(phrase), f"应识别为打断：{phrase!r}")
    for phrase in false_cases:
        check(not detect_reverse_signal(phrase), f"不应误杀：{phrase!r}")


# ---------------------------------------------------------------------------
# 用例 2：run() 入口直接返回，不启动工具循环
# ---------------------------------------------------------------------------
async def test_run_stops_on_signal():
    print("\n[2] run() 入口在打断信号时直接返回，不启动工具循环")
    cfg = ZhishuConfig()
    cfg.providers = {}
    cfg.default_model = ""
    sec = cfg.security
    ctx = ToolContext(kb=None, security=sec)
    agent = Agent(cfg, _FakeLLM(lambda n: {"choices": [{"message": {"content": "ok"}}]}), ctx=ctx)

    events = []
    async for ev in agent.run("停", session="s1"):
        events.append(ev)
    # 应产出 status + done（不进入工具循环），无 tool_call
    check(any(e.get("type") == "done" for e in events), "应产出 done 事件")
    check(all(e.get("type") != "tool_call" for e in events), "不应启动工具循环")


class _FakeLLM:
    def __init__(self, reply_fn):
        self.reply_fn = reply_fn

    async def chat(self, messages, model=None, tools=None, max_tokens=None, tool_choice=None):
        return self.reply_fn(0)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    test_phrases()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_run_stops_on_signal())
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
