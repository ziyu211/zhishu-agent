"""回归测试：create_skill 文本兜底（修复「模型贴 JSON 声称保存成功、技能页却看不到」，Task #651/#652）。

背景：内网实测——弱模型被要求「保存为 pb1 技能」时，并未真正发起 create_skill 工具调用，
而是把调用参数写成 ```json {...} ``` 文本块，并声称「✅ 技能已保存成功」。旧实现没有针对
create_skill-JSON 的恢复逻辑，于是技能永远落不了盘（功能模块技能列表里看不到），而模型却谎称成功。

本测试：
  · 纯函数 _extract_create_skill_args：覆盖 ```json 代码块 / 裸 JSON 对象 / 函数式 三种形变，
    并验证说明性短文本不误触发（content 长度护栏）；
  · 集成：驱动真实 Agent.run()，用假 LLM 产出含 create_skill JSON 文本块（无 tool_calls）的回复，
    断言系统通过 create_skill 真正执行并继续循环，最终基于真实返回给出结论（不再虚假成功）。
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
from zhishu.core.agent.agent import Agent, _extract_create_skill_args

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
    if name == "create_skill":
        return f"[create_skill] 已持久化技能 '{args.get('name')}' 到「功能模块技能」（磁盘保存，重启后仍在）。（私有，仅本人可见）"
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
    async for ev in agent.run("请把上面的方法保存为技能 pb1", session="s1", **run_kw):
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
# 纯函数测试：_extract_create_skill_args
# ---------------------------------------------------------------------------
def test_extract_json_block():
    print("\n[1] 提取 ```json 代码块中的 create_skill 参数")
    text = ("好的，我来调用 create_skill 保存为技能 pb1：\n"
            "```json\n"
            '{"name": "pb1", "content": "## 用途\\n排版文档\\n## 步骤\\n1. 读取\\n2. 转换", '
            '"description": "文档排版技能"}\n'
            "```")
    calls = _extract_create_skill_args(text)
    check(len(calls) == 1, "提取出 1 个 create_skill 调用")
    if calls:
        check(calls[0]["name"] == "pb1", "name 解析正确")
        check("## 步骤" in calls[0]["content"], "content 正文完整保留（含换行）")
        check(calls[0]["description"] == "文档排版技能", "description 解析正确")


def test_extract_bare_json_object():
    print("\n[2] 提取裸 JSON 对象（含 name + content）")
    text = '我来调用 create_skill 保存：{"name": "周报生成", "content": "这是一份较长的技能正文，包含多行说明与步骤描述用于测试提取是否稳定工作。", "shared": "true"}'
    calls = _extract_create_skill_args(text)
    check(len(calls) == 1, "提取出 1 个 create_skill 调用")
    if calls:
        check(calls[0]["name"] == "周报生成", "name 解析正确")
        check(calls[0]["shared"] is True, "shared=true 被正确解析为布尔 True")


def test_extract_function_form():
    print("\n[3] 提取函数式 create_skill(name=..., content=...)")
    text = 'create_skill(name="my_skill", content="这是一段足够长的技能正文内容用于验证函数式形变的提取是否稳定可靠。", description="示例")'
    calls = _extract_create_skill_args(text)
    check(len(calls) == 1, "提取出 1 个 create_skill 调用")
    if calls:
        check(calls[0]["name"] == "my_skill", "name 解析正确")
        check("足够长" in calls[0]["content"], "content 解析正确")


def test_no_false_trigger_on_mention():
    print("\n[4] 反向：仅提及 create_skill 但无 name+content JSON 不误触发")
    text = "你可以用 create_skill 工具来保存技能，需要时请直接调用。"
    calls = _extract_create_skill_args(text)
    check(len(calls) == 0, "无 JSON 对象时不触发")


def test_no_false_trigger_short_content():
    print("\n[5] 反向：content 过短（说明性短文本）不误触发")
    text = '```json\n{"name": "x", "content": "太短"}\n```'
    calls = _extract_create_skill_args(text)
    check(len(calls) == 0, "content 长度不足阈值时不触发（防误判说明文字）")


# ---------------------------------------------------------------------------
# 集成测试：弱模型贴 create_skill JSON → 系统兜底执行 create_skill
# ---------------------------------------------------------------------------
async def test_create_skill_fallback_executes():
    print("\n[6] 集成：模型贴 create_skill JSON（无 tool_calls）→ 系统兜底调用 create_skill 并执行")
    cfg = ZhishuConfig()
    agent = _make_agent(cfg)

    def rf(n):
        if n == 0:
            # 第一轮：弱模型把保存写成 JSON 文本块，未发起 function call，且声称成功
            return _text_resp(
                "✅ 技能已保存成功！下面用 create_skill 把它固化：\n"
                "```json\n"
                '{"name": "pb1", "content": "## 用途\\n对上传文档做统一排版\\n## 步骤\\n1. 用 read_file 读取\\n2. 用 code_exec + python-docx 排版\\n3. 交付 /media 链接", "description": "文档排版技能"}\n'
                "```"
            )
        # 第二轮：基于真实返回给最终结论（不含 create_skill JSON，结束）
        return _text_resp("已为您保存技能 pb1（文档排版技能），您可在功能模块技能中查看与开关。")

    events = await _collect(agent, rf, user_role="operator")
    cs_calls = [a for (nm, a) in _EXEC_LOG if nm == "create_skill"]
    check(len(cs_calls) == 1, "create_skill 被兜底调用恰好 1 次")
    if cs_calls:
        check(cs_calls[0].get("name") == "pb1", "执行的是模型贴出的真实技能名 pb1")
        check("python-docx" in cs_calls[0].get("content", ""), "执行的是模型贴出的真实技能正文")
    has_tool_call = any(e.get("type") == "tool_call" and e.get("name") == "create_skill"
                        for e in events)
    check(has_tool_call, "事件流包含 create_skill 的 tool_call 事件（前端可见执行卡片）")
    ended = any(e.get("type") == "done" for e in events)
    check(ended, "对话正常结束（模型基于真实落盘结果给出结论，而非虚假成功）")


async def test_create_skill_fallback_no_double_exec():
    print("\n[7] 集成：同一次回复对已落盘同名技能不重复执行")
    cfg = ZhishuConfig()
    agent = _make_agent(cfg)

    def rf(n):
        if n == 0:
            return _text_resp(
                'create_skill 参数如下：\n```json\n'
                '{"name": "dup", "content": "这是一段用于验证不重复执行的技能正文内容，足够长以通过阈值校验。"}\n'
                '```\n第二次又贴了同样的 create_skill 参数：\n```json\n'
                '{"name": "dup", "content": "这是一段用于验证不重复执行的技能正文内容，足够长以通过阈值校验。"}\n'
                '```'
            )
        return _text_resp("已完成。")

    events = await _collect(agent, rf, user_role="operator")
    cs_calls = [a for (nm, a) in _EXEC_LOG if nm == "create_skill"]
    check(len(cs_calls) == 1, "同名技能即使贴两次也只落盘 1 次（去重护栏）")
    ended = any(e.get("type") == "done" for e in events)
    check(ended, "对话正常结束")


async def async_main():
    test_extract_json_block()
    test_extract_bare_json_object()
    test_extract_function_form()
    test_no_false_trigger_on_mention()
    test_no_false_trigger_short_content()
    await test_create_skill_fallback_executes()
    await test_create_skill_fallback_no_double_exec()
    print(f"\n==== 通过 {PASS} / 失败 {len(FAIL)} ====")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(async_main())
