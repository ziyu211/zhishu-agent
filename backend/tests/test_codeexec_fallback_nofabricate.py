"""回归：code_exec 代码块兜底注入不得诱导模型编造执行结果（qwen3.5 幻觉缺陷）。

背景（v1.0.55 修复，来源：内网部署 + qwen3.5 122b 实测缺陷）

    原实现在「模型只贴 Python 代码块、未真正调用 code_exec」时，会注入一条
    ``role=user`` 的提示::

        [系统] 你刚才提供的 Python 代码已���系统通过 code_exec 工具实际执行，
        执行结果见上方工具返回。请基于真实执行结果进行分析与总结...

    该消息有两个叠加缺陷：

    1. **角色语义错配**：内容是系统通知，却用 ``role=user`` 注入（`system_at_beginning`
       默认为 True，改 system 会被 ``merge_system_messages`` 置顶而时序错乱，故必须
       保持 user 角色）。qwen3.5 122b 因此认定「用户提到：代码已执行」→ 幻觉。
    2. **表述不自包含**：「结果见上方工具返回」依赖 tool 消息可见。一旦该消息在
       compat 层被 ``flatten_tool_messages`` 摊平、被 ``enforce_alternating`` 合并，
       或执行结果本身为空，模型完全看不到结果 → 只能编造。

    修复策略：保持 ``role=user`` 以维持时序位置，但
    ① 显式标注「非用户发言」；② 内联真实返回（自包含）；③ 空结果如实告知并禁止编造。

本测试在源码层锁定这三条约束，防止回归。
"""
from __future__ import annotations

import os

AGENT_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "zhishu", "core", "agent", "agent.py",
)


def _src() -> str:
    with open(AGENT_PY, encoding="utf-8") as f:
        return f.read()


def test_code_exec_fallback_marks_not_user_utterance():
    """兜底注入必须显式声明「非用户发言」，否则模型会误认成用户说的话。"""
    src = _src()
    assert "系统通知 · 非用户发言" in src, (
        "code_exec 兜底注入缺少「非用户发言」标识：role=user 的系统通知会被模型"
        "（如 qwen3.5 122b）误认为用户发言，进而声称「用户提到代码已执行」→ 幻觉"
    )


def test_code_exec_fallback_is_self_contained():
    """不得再出现「结果见上方工具返回」这类非自包含表述。"""
    src = _src()
    assert "执行结果见上方工具返回" not in src, (
        "仍存在非自包含表述「执行结果见上方工具返回」：一旦 tool 消息在 compat 层被"
        "摊平/合并，或执行结果为空，模型将看不到结果而编造 → 幻觉"
    )
    assert "_exec_out" in src, "兜底注入未内联 code_exec 真实返回（应自包含）"


def test_code_exec_fallback_no_fabrication_when_empty():
    """结果为空时必须如实告知，并明确禁止编造，不得无条件断言「已实际执行」。"""
    src = _src()
    assert "工具未返回任何输出" in src, "空结果时缺少如实告知措辞"
    assert "不得编造或臆测执行结果" in src, "缺少明确禁止编造执行结果的指令"
