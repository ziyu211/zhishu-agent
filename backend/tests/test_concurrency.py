"""企业级并发/配额限流单元测试（P0-1）。

验证 ConcurrencyLimiter 的：无限制直通、全局并发上限、单用户并发上限、
单用户每日配额拒绝与落盘持久化、释放后恢复。
不依赖任何 LLM / 外部服务，纯 asyncio + 文件系统。
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from zhishu.core.concurrency import ConcurrencyLimiter, ConcurrencyLimitError


async def _expect_blocked(task, timeout: float = 0.2):
    """确认 task 在 timeout 内仍挂起（不被调度）。注意：asyncio.wait 超时不会取消任务。"""
    done, pending = await asyncio.wait({task}, timeout=timeout)
    assert task in pending, "任务应仍被阻塞"


async def test_no_limit_passthrough():
    lim = ConcurrencyLimiter()
    lim.configure()  # 全 0 = 不限制
    await asyncio.gather(*[lim.acquire(f"u{i}") for i in range(5)])
    for i in range(5):
        await lim.release(f"u{i}")


async def test_global_limit():
    lim = ConcurrencyLimiter()
    lim.configure(global_limit=2)
    await lim.acquire("a")
    await lim.acquire("a")
    # 第三个应被全局上限阻塞
    task = asyncio.create_task(lim.acquire("a"))
    await _expect_blocked(task)
    # 释放一个槽位后，第三个得以通过
    await lim.release("a")
    await asyncio.wait_for(task, timeout=0.5)
    await lim.release("a")
    await lim.release("a")


async def test_per_user_limit():
    lim = ConcurrencyLimiter()
    lim.configure(per_user_limit=1)
    await lim.acquire("alice")
    # 同一用户第二个并发被挡
    t = asyncio.create_task(lim.acquire("alice"))
    await _expect_blocked(t)
    # 不同用户不受影响
    await lim.acquire("bob")
    await lim.release("alice")
    await asyncio.wait_for(t, timeout=0.5)  # alice 现在能通过
    await lim.release("bob")
    await lim.release("alice")


async def test_daily_quota_rejects_and_persists():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "quota_usage.json")
    lim = ConcurrencyLimiter()
    lim.configure(daily_quota=2, quota_path=path)
    await lim.acquire("u")
    await lim.release("u")
    await lim.acquire("u")
    await lim.release("u")
    # 第三次应被拒（额度 2/天）
    with pytest.raises(ConcurrencyLimitError):
        await lim.acquire("u")
    # 落盘文件已生成
    assert os.path.exists(path)
    import json
    import time
    data = json.load(open(path, encoding="utf-8"))
    today = time.strftime("%Y-%m-%d")
    assert data.get(today, {}).get("u") == 2


async def test_release_restores_capacity():
    lim = ConcurrencyLimiter()
    lim.configure(global_limit=1)
    await lim.acquire("x")
    t = asyncio.create_task(lim.acquire("x"))
    await _expect_blocked(t)
    await lim.release("x")
    await asyncio.wait_for(t, timeout=0.5)
    await lim.release("x")
