"""回归测试：代码块 → code_exec 兜底（修复「贴代码不执行」回归，Task #433/#434）。

背景：部分国产模型在被要求「运行 Python」时，不会真正发起 function call，而是把代码
写成 Markdown 代码块（如 requests/akshare 抓行情）直接作为回复抛出。旧实现没有针对
代码块的恢复逻辑（只有 delegate_to_agent 的文本兜底），于是代码块被当最终答案返回，
用户只看到源码却拿不到执行结果——即用户报告的「不执行任务」退化表现。

本测试：
  · 纯函数 _extract_python_blocks：仅提取显式 python/py 标注块，排除 json/bash/无标注块；
  · 集成：驱动真实 Agent.run()，用假 LLM 产出含 ```python 代码块（无 tool_calls）的回复，
    断言系统通过 code_exec 真正执行了该代码并继续循环，最终基于结果给出结论。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import zhishu.core.agent.agent as agent_mod
from zhishu.core.tools.base import ToolContext
from zhishu.core.config import ZhishuConfig
from zhishu.core.agent.agent import Agent, _extract_python_blocks

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
    if name == "code_exec":
        return "特变电工(600089) 最新价: 12.34  涨跌幅: +1.23%"
    return "ok"


agent_mod.ToolRegistry.execute = _fake_execute


def _text_resp(text):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": text, "tool_calls": None},
            "finish_reason": "stop",
        }]
    }


class _FakeLLM:
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
    async for ev in agent.run("拉取最新数据分析特变电工（600089）", session="s1", **run_kw):
        events.append(ev)
    return events


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
# 纯函数测试：_extract_python_blocks
# ---------------------------------------------------------------------------
def test_extract_single_python():
    print("\n[1] 提取单个 ```python 块")
    text = "先抓数据：\n```python\nimport requests\nprint(requests.get('https://qt.gtimg.cn/q=sh600089').text)\n```\n再分析"
    blocks = _extract_python_blocks(text)
    check(len(blocks) == 1, "提取出 1 个代码块")
    check("requests.get" in blocks[0], "代码内容完整保留（含 requests.get）")


def test_extract_py_alias_and_case_insensitive():
    print("\n[2] 支持 ```py 别名与各大小写")
    text = "```py\nprint(1)\n```\n```Python\nprint(2)\n```"
    blocks = _extract_python_blocks(text)
    check(len(blocks) == 2, "py 别名与 Python 大小写均被识别")
    check("print(1)" in blocks[0] and "print(2)" in blocks[1], "两段代码均保留")


def test_exclude_other_langs_and_empty():
    print("\n[3] 排除 json/bash/无标注块与空块")
    text = (
        "```json\n{\"a\": 1}\n```\n"
        "```bash\nls -la\n```\n"
        "```python\n\n```\n"  # 空块应忽略
        "普通文本里有 `code` 行内代码也不应命中"
    )
    blocks = _extract_python_blocks(text)
    check(len(blocks) == 0, "不含任何 python/py 块时为 0（json/bash/空/行内均排除）")


def test_mixed_blocks_keep_only_python():
    print("\n[4] 混合多语言块只保留 python")
    text = "```json\n{}\n```\n```python\nimport akshare as ak\nprint(ak.stock_zh_a_hist_tx('600089'))\n```\n```yaml\nx: 1\n```"
    blocks = _extract_python_blocks(text)
    check(len(blocks) == 1, "仅 1 个 python 块")
    check("akshare" in blocks[0], "保留 akshare 代码")


# ---------------------------------------------------------------------------
# 集成测试：弱模型贴代码块 → 系统兜底执行 code_exec
# ---------------------------------------------------------------------------
async def test_codeblock_fallback_executes():
    print("\n[5] 集成：模型贴 ```python 块（无 tool_calls）→ 系统兜底调用 code_exec 并执行")
    cfg = ZhishuConfig()
    cfg.security.allow_code_exec = True
    agent = _make_agent(cfg)

    def rf(n):
        if n == 0:
            # 第一轮：弱模型把抓数代码写成 Markdown 代码块，未发起 function call
            return _text_resp(
                "好的，我来拉取行情：\n"
                "```python\n"
                "import requests\n"
                "r = requests.get('https://qt.gtimg.cn/q=sh600089')\n"
                "print(r.text)\n"
                "```\n"
            )
        # 第二轮：基于执行结果给最终结论（不含 python 块，结束）
        return _text_resp("根据执行结果，特变电工(600089) 最新价为 12.34，涨跌幅 +1.23%，建议……")

    events = await _collect(agent, rf, user_role="operator")
    code_exec_calls = [a for (nm, a) in _EXEC_LOG if nm == "code_exec"]
    check(len(code_exec_calls) == 1, "code_exec 被兜底调用恰好 1 次")
    if code_exec_calls:
        check("requests.get" in code_exec_calls[0].get("code", ""),
              "执行的是模型贴出的真实 Python 代码")
    has_tool_call = any(e.get("type") == "tool_call" and e.get("name") == "code_exec"
                        for e in events)
    check(has_tool_call, "事件流包含 code_exec 的 tool_call 事件（前端可见执行卡片）")
    ended = any(e.get("type") == "done" for e in events)
    check(ended, "对话正常结束（模型基于真实结果给出结论，而非空转）")


async def test_codeblock_fallback_respects_permission():
    print("\n[6] 反向：无 code_exec 授权的角色（user/viewer）不会被兜底执行")
    cfg = ZhishuConfig()
    cfg.security.allow_code_exec = True
    agent = _make_agent(cfg)

    def rf(n):
        if n == 0:
            return _text_resp("```python\nprint('should-not-run')\n```")
        return _text_resp("（最终结论）")

    # viewer 角色：不在 admin/operator 之列 → _code_exec_allowed 为 False → 不兜底
    events = await _collect(agent, rf, user_role="viewer")
    code_exec_calls = [a for (nm, a) in _EXEC_LOG if nm == "code_exec"]
    check(len(code_exec_calls) == 0, "viewer 角色的 python 块未被兜底执行（权限 fail-closed）")
    ended = any(e.get("type") == "done" for e in events)
    check(ended, "对话仍正常结束")


async def async_main():
    test_extract_single_python()
    test_extract_py_alias_and_case_insensitive()
    test_exclude_other_langs_and_empty()
    test_mixed_blocks_keep_only_python()
    await test_codeblock_fallback_executes()
    await test_codeblock_fallback_respects_permission()
    print(f"\n==== 通过 {PASS} / 失败 {len(FAIL)} ====")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(async_main())
