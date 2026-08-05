"""回归测试：Agent.__init__ 必须把构造入参 media 注入到 ctx.media。

历史回归（闭环 H1）：生产链路传入的 ctx 恒为非 None 的 ToolContext 实例，且 ToolContext
未定义 __bool__，导致 `ctx or ToolContext(...)` 永远取 ctx，构造入参 media 被丢弃，
self.ctx.media 变为 None，工具无法自动发布 /media 下载链接、下载护栏自愈失效。

本测试直接经 Agent(...) 构造（而非直传带 media 的 ctx），复现并守住该回归。
"""
from __future__ import annotations

import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from zhishu.core.tools.base import ToolContext
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


class _FakeMedia:
    pass


class _FakeLLM:
    pass


def _cfg():
    return types.SimpleNamespace(
        security=types.SimpleNamespace(),
        agent=types.SimpleNamespace(max_tokens=1024),
    )


def test_media_injected_when_ctx_has_none():
    print("\n[1] ctx 自带 media=None 时，构造入参 media 应被注入 ctx.media")
    cfg = _cfg()
    media = _FakeMedia()
    ctx = ToolContext(kb=None, security=cfg.security)  # media 默认 None
    agent = Agent(cfg, _FakeLLM(), ctx=ctx, media=media)
    check(agent.ctx.media is media, "agent.ctx.media 被注入为构造入参 media")


def test_media_injected_when_ctx_none():
    print("\n[2] 未传 ctx 时，应新建 ToolContext 并注入 media")
    cfg = _cfg()
    media = _FakeLLM()  # 占位，仅用于身份判断，下面用真正的 _FakeMedia
    media = _FakeMedia()
    agent = Agent(cfg, _FakeLLM(), media=media)
    check(agent.ctx is not None, "Agent 自带 ctx 已创建")
    check(agent.ctx.media is media, "新建 ctx.media 为构造入参 media")


def test_existing_ctx_media_preserved():
    print("\n[3] ctx 已携带 media 时，不应被覆盖")
    cfg = _cfg()
    media_a = _FakeMedia()
    media_b = _FakeMedia()
    ctx = ToolContext(kb=None, security=cfg.security, media=media_a)
    agent = Agent(cfg, _FakeLLM(), ctx=ctx, media=media_b)
    check(agent.ctx.media is media_a, "优先保留 ctx 自带的 media")


def test_media_survives_for_run():
    print("\n[4] for_run 派生副本应保留注入的 media（防串号隔离的前提）")
    cfg = _cfg()
    media = _FakeMedia()
    ctx = ToolContext(kb=None, security=cfg.security)  # media None
    agent = Agent(cfg, _FakeLLM(), ctx=ctx, media=media)
    run_ctx = agent.ctx.for_run("alice", "s1", is_admin=False, user_role="user")
    check(run_ctx.media is media, "for_run 后 media 仍为注入的 media 实例")


if __name__ == "__main__":
    test_media_injected_when_ctx_has_none()
    test_media_injected_when_ctx_none()
    test_existing_ctx_media_preserved()
    test_media_survives_for_run()
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")
