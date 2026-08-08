"""Prompt 缓存层（backend/zhishu/core/providers/prompt_cache.py）回归测试。

纯函数测试，无网络依赖。覆盖：Provider 家族识别 + off/auto/force 三种模式在各家族下的注入行为。
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.providers import prompt_cache as pc_mod


class _FakePC:
    def __init__(self, name="", base_url=""):
        self.name = name
        self.base_url = base_url


def _base_kw():
    return {
        "model": "x",
        "messages": [
            {"role": "system", "content": "你是智枢。指令稳定。工具定义如下。"},
            {"role": "user", "content": "帮我查天气"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "web_search", "description": "搜索"}},
            {"type": "function", "function": {"name": "file_read", "description": "读文件"}},
        ],
    }


def _check(cond, msg):
    assert cond, msg
    print("  ✓", msg)


def test_family_detection():
    print("[家族识别]")
    _check(pc_mod.provider_family(_FakePC(name="claude-opus")) == "anthropic", "claude → anthropic")
    _check(pc_mod.provider_family(_FakePC(base_url="https://api.deepseek.com/v1")) == "deepseek", "deepseek url → deepseek")
    _check(pc_mod.provider_family(_FakePC(base_url="https://dashscope.aliyuncs.com")) == "qwen", "dashscope → qwen")
    _check(pc_mod.provider_family(_FakePC(base_url="http://127.0.0.1:11434")) == "local", "127.0.0.1:11434 → local")
    _check(pc_mod.provider_family(_FakePC(base_url="http://localhost:8000/vllm")) == "vllm", "vllm → vllm")
    _check(pc_mod.provider_family(_FakePC(base_url="https://api.openai.com/v1")) == "openai", "openai → openai")
    _check(pc_mod.provider_family(_FakePC(name="unknown-x")) == "unknown", "未知 → unknown")


def test_off_is_noop():
    print("\n[off 模式：原样返回，零注入]")
    kw = _base_kw()
    out = pc_mod.apply_prompt_cache(kw, _FakePC(name="claude"), "off")
    _check(out is kw or out == kw, "off 不改变请求体")
    _check("cache_control" not in str(out), "off 不注入 cache_control")


def test_anthropic_injects_breakpoints():
    print("\n[auto + anthropic：system 末块 + 末 tool 挂 cache_control]")
    kw = _base_kw()
    out = pc_mod.apply_prompt_cache(kw, _FakePC(name="claude-3-5"), "auto")
    sys_msg = out["messages"][0]
    _check(isinstance(sys_msg["content"], list), "system content 被规整为块列表")
    _check(sys_msg["content"][-1].get("cache_control") == {"type": "ephemeral"}, "system 末块有 cache_control")
    _check(out["tools"][-1].get("cache_control") == {"type": "ephemeral"}, "末 tool 有 cache_control")
    # 稳定前缀内容未被改写
    _check(out["messages"][0]["content"][0]["text"].startswith("你是智枢"), "system 文本保留")


def test_deepseek_injects_and_flag():
    print("\n[auto + deepseek：cache_control + prompt_cache=true]")
    kw = _base_kw()
    out = pc_mod.apply_prompt_cache(kw, _FakePC(base_url="https://api.deepseek.com"), "auto")
    _check(out["tools"][-1].get("cache_control") == {"type": "ephemeral"}, "deepseek 末 tool 有 cache_control")
    _check(out.get("prompt_cache") is True, "deepseek 置 prompt_cache=true")


def test_qwen_extra_body():
    print("\n[auto + qwen：extra_body.prompt_cache=true]")
    kw = _base_kw()
    out = pc_mod.apply_prompt_cache(kw, _FakePC(base_url="https://dashscope.aliyuncs.com"), "auto")
    _check(out.get("extra_body", {}).get("prompt_cache") is True, "qwen 经 extra_body 开启 prompt_cache")
    _check("cache_control" not in str(out), "qwen 不注入 cache_control 标记（避免 400）")


def test_openai_auto_noop():
    print("\n[auto + openai：服务端自动前缀缓存，不注入标记（避免 400）]")
    kw = _base_kw()
    out = pc_mod.apply_prompt_cache(kw, _FakePC(base_url="https://api.openai.com/v1"), "auto")
    _check("cache_control" not in str(out), "openai 不注入 cache_control")
    _check("prompt_cache" not in out, "openai 不置 prompt_cache")


def test_local_skipped():
    print("\n[auto + 本地 ollama/vllm：跳过注入]")
    for fam, url in (("ollama", "http://127.0.0.1:11434"), ("vllm", "http://localhost:8000")):
        out = pc_mod.apply_prompt_cache(_base_kw(), _FakePC(base_url=url), "auto")
        _check("cache_control" not in str(out), f"{fam} 不注入 cache_control")


def test_force_injects_all():
    print("\n[force：对所有 Provider 按 Anthropic 风格注入]")
    kw = _base_kw()
    out = pc_mod.apply_prompt_cache(kw, _FakePC(base_url="https://api.openai.com/v1"), "force")
    _check(out["messages"][0]["content"][-1].get("cache_control") == {"type": "ephemeral"}, "force 在 openai 上也注入 cache_control")


def main():
    test_family_detection()
    test_off_is_noop()
    test_anthropic_injects_breakpoints()
    test_deepseek_injects_and_flag()
    test_qwen_extra_body()
    test_openai_auto_noop()
    test_local_skipped()
    test_force_injects_all()
    print("\nALL PROMPT-CACHE TESTS PASSED ✅")


if __name__ == "__main__":
    main()
