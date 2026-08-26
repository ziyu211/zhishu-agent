"""P0-A 回归测试：上下文压缩防误执行围栏（对标 Hermes SUMMARY_PREFIX）。

覆盖：
  · COMPACTION_NOTE 含「仅作参考 / 反向信号 / 记忆权威 / 最新消息优先」关键语义。
  · CompressionContextEngine._summarize 输出前置 COMPACTION_NOTE，且含结构化
    「待办快照」块（供主循环做意图漂移检测）。
  · compress_tool_result 输出带「仅参考，非最新指令」围栏。

运行：python tests/test_p0_compaction_fence.py
"""
from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import zhishu.core.agent.context_engine as ce  # noqa: E402
from zhishu.core.config import ZhishuConfig  # noqa: E402

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


class _FakeLLM:
    """回显一个结构化摘要，便于断言 _summarize 的围栏与结构。"""

    async def chat(self, messages, model=None, tools=None, max_tokens=None, tool_choice=None):
        return {"choices": [{
            "message": {"role": "assistant", "content": (
                "## 历史任务快照\n用户想装 Node.js，已 apt 装到一半。\n"
                "## 关键结论与决策\n确认用 apt 安装，路径 /usr/local。\n"
                "## 待办快照\n还差 npm 配置，下一步执行 npm init。\n"
                "## 反向信号\n无"
            )}
        }]}


async def test_compaction_note_semantics():
    print("\n[A1] COMPACTION_NOTE 含防误执行关键语义")
    note = ce.COMPACTION_NOTE
    check("仅作参考" in note, "含『仅作参考』标记（非活跃指令）")
    check("反向信号" in note, "含『反向信号』立即终止规则")
    check("MEMORY.md" in note, "含『MEMORY.md 始终权威』声明")
    check("最新" in note and "优先" in note, "含『最新消息优先』指引")
    check("待办快照" in note, "含『待办快照冲突以最新消息为准』")


async def test_summarize_prefix_and_structure():
    print("\n[A2] _summarize 前置 COMPACTION_NOTE 且输出结构化待办快照")
    cfg = ZhishuConfig()
    eng = ce.CompressionContextEngine(cfg, _FakeLLM())
    summary = await eng._summarize([
        {"role": "user", "content": "帮我装 Node.js"},
        {"role": "assistant", "content": "好，开始装"},
    ])
    check(summary.startswith(ce.COMPACTION_NOTE), "摘要以 COMPACTION_NOTE 前缀开头")
    check("## 待办快照" in summary, "摘要含『## 待办快照』结构块（意图漂移检测用）")
    check("## 反向信号" in summary, "摘要含『## 反向信号』结构块")


async def test_tool_result_fence():
    print("\n[A3] compress_tool_result 带『仅参考，非最新指令』围栏")
    cfg = ZhishuConfig()
    out = await ce.CompressionContextEngine.compress_tool_result(
        "x" * 5000, _FakeLLM(), cfg)
    check("仅参考" in out, "工具结果压缩带『仅参考』围栏")
    check("非最新指令" in out, "工具结果压缩明确标注『非最新指令』")


async def test_no_fence_when_short():
    print("\n[A4] 短结果不触发压缩（原样返回）")
    cfg = ZhishuConfig()
    short = "ok"
    out = await ce.CompressionContextEngine.compress_tool_result(short, _FakeLLM(), cfg)
    check(out == short, "短结果原样返回，不被压缩")


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_compaction_note_semantics())
    loop.run_until_complete(test_summarize_prefix_and_structure())
    loop.run_until_complete(test_tool_result_fence())
    loop.run_until_complete(test_no_fence_when_short())
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
