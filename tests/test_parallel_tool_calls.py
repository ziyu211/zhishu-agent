# -*- coding: utf-8 -*-
# 回归测试：parallel_tool_calls 信号的下发 / 剥除逻辑。
#
# 这是「智枢比 Hermes 慢」的核心修复点：框架早已支持单回合多工具并发
# （agent.py 的 _do_parallel + asyncio.gather），但请求体里从未下发
# parallel_tool_calls: true，模型收不到「可并行发多个工具调用」的信号，
# 于是每个回合只发 1 个工具调用、N 次串行 LLM 往返。
#
# 本测试验证 compat.sanitize_kwargs 的两条分支：
#   1) Provider 支持并行  -> 请求体必须带上 parallel_tool_calls: True
#   2) Provider 不支持    -> 请求体必须剥除该参数（且自愈回路会落到 drop_params）
#
# 既可直接 `python tests/test_parallel_tool_calls.py` 运行，也可被 pytest 收集。
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from zhishu.core.providers import compat
from zhishu.core.providers.compat import CompatProfile, sanitize_kwargs


def _profile(supports_parallel: bool, drop=()):
    return CompatProfile(
        key="test", label="test",
        supports_tools=True, supports_stream_options=True,
        supports_parallel_tool_calls=supports_parallel,
        drop_params=drop,
    )


def test_parallel_enabled_adds_flag():
    """支持并行的 Provider，请求体应带上 parallel_tool_calls: True。"""
    prof = _profile(supports_parallel=True)
    kw = sanitize_kwargs({"model": "x", "tools": [{}]}, prof, tools_enabled=True)
    assert kw.get("parallel_tool_calls") is True, kw


def test_parallel_disabled_strips_flag():
    """不支持并行的 Provider，请求体不得出现 parallel_tool_calls。"""
    prof = _profile(supports_parallel=False)
    kw = sanitize_kwargs(
        {"model": "x", "tools": [{}], "parallel_tool_calls": True}, prof,
        tools_enabled=True)
    assert "parallel_tool_calls" not in kw, kw


def test_no_tools_never_adds_flag():
    """即便 Provider 支持并行，未启用工具时也不应下发该参数。"""
    prof = _profile(supports_parallel=True)
    kw = sanitize_kwargs({"model": "x"}, prof, tools_enabled=False)
    assert "parallel_tool_calls" not in kw, kw


def test_runtime_cap_no_optional_strips_flag():
    """运行期学到「拒绝可选参数」(CAP_NO_OPTIONAL) 后，画像降级应剥除该参数。"""
    import types
    # 假 Provider：compat=openai（静态画像并行=True）；带 base_url 以便 runtime_caps 命中。
    pc = types.SimpleNamespace(base_url="http://h:8000/v1", compat="openai", name="x")
    prof = compat.effective_profile(pc, "m")
    assert prof.supports_parallel_tool_calls is True  # 静态画像仍允许
    compat.runtime_caps.add(compat.RuntimeCaps.key("http://h:8000/v1", "m"),
                            compat.CAP_NO_OPTIONAL)
    prof2 = compat.effective_profile(pc, "m")
    assert prof2.supports_parallel_tool_calls is False  # 学到后降级
    kw = sanitize_kwargs({"model": "m", "tools": [{}], "parallel_tool_calls": True},
                         prof2, tools_enabled=True)
    assert "parallel_tool_calls" not in kw, kw
    compat.runtime_caps.clear()  # 清理，避免污染其它测试


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL  {t.__name__}: {e}")
    if failed:
        print(f"\n{len(failed)}/{len(tests)} FAILED")
        sys.exit(1)
    print(f"\nALL_TESTS_PASSED ({len(tests)})")


if __name__ == "__main__":
    main()
