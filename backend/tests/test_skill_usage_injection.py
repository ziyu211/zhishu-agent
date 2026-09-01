"""技能使用统计（方案 A）：注入系统提示生效时按会话去重 +1。

背景：use_count 原先只在 read_skill 被调用时自增，而 enabled 技能的正文会直接
注入系统提示，模型按注入指令行动、不再调用 read_skill，导致统计恒为 0（内网
实测症状）。本文件锁定「注入即计数 + 会话去重」的行为。
"""
from __future__ import annotations

import json
import os

import pytest

from zhishu.core.modules import skills as sk


def _mk(tmp_path, name: str, content: str = "指令正文") -> str:
    d = os.path.join(str(tmp_path), name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "content": content, "enabled": True}, f,
                  ensure_ascii=False, indent=2)
    return d


def _cnt(tmp_path, name: str):
    fp = os.path.join(str(tmp_path), name, "module.json")
    if not os.path.isfile(fp):
        return None
    return json.load(open(fp, encoding="utf-8")).get("use_count")


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    """把技能根指向临时目录，并隔离会话去重状态，避免用例间互相污染。"""
    monkeypatch.setattr(sk, "_skills_root", lambda: str(tmp_path))
    sk._USAGE_SESSIONS.clear()
    yield
    sk._USAGE_SESSIONS.clear()


def test_injection_counts_once(tmp_path):
    _mk(tmp_path, "demo")
    assert sk.touch_skill_usage("demo", session_id="s1") is True
    assert _cnt(tmp_path, "demo") == 1


def test_same_session_dedup(tmp_path):
    """同一会话多轮对话只计一次，避免数字虚高。"""
    _mk(tmp_path, "demo")
    for _ in range(5):
        sk.touch_skill_usage("demo", session_id="s1")
    assert _cnt(tmp_path, "demo") == 1


def test_new_session_counts_again(tmp_path):
    """换一个会话再次注入 → 再 +1（反映「有多少会话用到了该技能」）。"""
    _mk(tmp_path, "demo")
    sk.touch_skill_usage("demo", session_id="s1")
    sk.touch_skill_usage("demo", session_id="s2")
    assert _cnt(tmp_path, "demo") == 2


def test_last_used_recorded(tmp_path):
    _mk(tmp_path, "demo")
    sk.touch_skill_usage("demo", session_id="s1")
    fp = os.path.join(str(tmp_path), "demo", "module.json")
    assert json.load(open(fp, encoding="utf-8")).get("last_used")


def test_missing_skill_returns_false(tmp_path):
    assert sk.touch_skill_usage("nope", session_id="s1") is False


def test_chinese_name_supported(tmp_path):
    """中文技能名不被 sanitize 误删（用户常以中文命名技能）。"""
    _mk(tmp_path, "写周报")
    assert sk.touch_skill_usage("写周报", session_id="s1") is True
    assert _cnt(tmp_path, "写周报") == 1


def test_path_traversal_blocked(tmp_path):
    assert sk.touch_skill_usage("../evil", session_id="s1") is False
    assert sk.touch_skill_usage("a/b", session_id="s1") is False
    assert sk.touch_skill_usage("..", session_id="s1") is False


def _cfg(progressive: bool = False):
    return type("C", (), {"agent": type("A", (), {"skills_progressive": progressive})()})()


def test_build_context_counts_injected_skills(tmp_path, monkeypatch):
    """集成：全文注入模式下，构建系统提示即计数；同会话不重复计。"""
    _mk(tmp_path, "demo", content="指令正文")
    monkeypatch.setattr(
        sk, "_enabled_skills",
        lambda *a, **k: [{"name": "demo", "description": "d", "content": "指令正文"}],
    )
    monkeypatch.setattr(sk, "_read_memory_files", lambda *a, **k: [])

    out1 = sk.build_agent_context_prompt(_cfg(), owner="u", include_memory=False,
                                         session_id="sess-1")
    assert "demo" in out1 and "指令正文" in out1   # 确实注入了
    assert _cnt(tmp_path, "demo") == 1

    # 同一会话再构建一次：注入仍在，但不重复计数
    out2 = sk.build_agent_context_prompt(_cfg(), owner="u", include_memory=False,
                                         session_id="sess-1")
    assert "demo" in out2
    assert _cnt(tmp_path, "demo") == 1

    # 新会话 → 再 +1
    sk.build_agent_context_prompt(_cfg(), owner="u", include_memory=False,
                                  session_id="sess-2")
    assert _cnt(tmp_path, "demo") == 2


def test_progressive_mode_counts_listed_skills(tmp_path, monkeypatch):
    """渐进披露模式（只注入清单）同样计数。"""
    _mk(tmp_path, "demo", content="指令正文")
    monkeypatch.setattr(
        sk, "_enabled_skills",
        lambda *a, **k: [{"name": "demo", "description": "d", "content": "指令正文"}],
    )
    monkeypatch.setattr(sk, "_read_memory_files", lambda *a, **k: [])
    out = sk.build_agent_context_prompt(_cfg(progressive=True), owner="u",
                                        include_memory=False, session_id="sess-1")
    assert "- demo: d" in out
    assert _cnt(tmp_path, "demo") == 1


def test_skill_without_content_not_counted(tmp_path, monkeypatch):
    """全文模式下无正文的技能不会被注入，因此也不应计数。"""
    _mk(tmp_path, "empty", content="")
    monkeypatch.setattr(
        sk, "_enabled_skills",
        lambda *a, **k: [{"name": "empty", "description": "d", "content": ""}],
    )
    monkeypatch.setattr(sk, "_read_memory_files", lambda *a, **k: [])
    sk.build_agent_context_prompt(_cfg(), owner="u", include_memory=False,
                                  session_id="sess-1")
    assert _cnt(tmp_path, "empty") is None
