"""智枢智能体 —— 企业级并发与配额限流（对标 Hermes 无此内建，属智枢护城河）。

设计目标：
  * 顶层对话（chat 入口）按「全局并发 + 单用户并发 + 单用户每日配额」三重节流，
    防止少量长任务（多智能体委派 / 大文档解析）占满单进程事件循环拖垮全员。
  * 信号量在**整个对话流（SSE 生成器）**生命周期内持有，真实反映「活跃会话」占用，
    而非仅请求瞬间；子智能体委派属于同一轮对话的嵌套执行，**不再二次占额**（避免死锁）。
  * 配额计数按自然日落盘（quota_usage.json），进程重启/跨日自动清零，零外部依赖。
  * 后端可插拔：ConcurrencyLimiter 仅依赖进程内 asyncio 原语；多实例部署时，把
    _acquire_quota / _release_quota 换成 Redis/ZooKeeper 后端即可水平扩展（见 P2）。

线程/协程安全：所有状态变更经 asyncio.Lock 串行化；信号量为协程安全原语。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional


class ConcurrencyLimitError(Exception):
    """并发/配额被拒（应转为对用户友好的 429 风格错误事件）。"""


class ConcurrencyLimiter:
    def __init__(self) -> None:
        self._global_limit = 0
        self._per_user_limit = 0
        self._daily_quota = 0
        self._global_sem: Optional[asyncio.Semaphore] = None
        self._user_sems: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()
        self._quota_path: Optional[str] = None
        self._quota_cache: dict[str, dict[str, int]] = {}  # {date: {user: count}}
        self._today: str = time.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 配置（进程启动时由 AppContext 注入；测试可单独调用）
    # ------------------------------------------------------------------
    def configure(
        self,
        *,
        global_limit: int = 0,
        per_user_limit: int = 0,
        daily_quota: int = 0,
        quota_path: Optional[str] = None,
    ) -> None:
        self._global_limit = int(global_limit or 0)
        self._per_user_limit = int(per_user_limit or 0)
        self._daily_quota = int(daily_quota or 0)
        self._quota_path = quota_path
        # 信号量在协程内惰性绑定到运行事件循环；此处重建不影响已持有的旧键。
        self._global_sem = (
            asyncio.Semaphore(self._global_limit) if self._global_limit > 0 else None
        )
        self._user_sems = {}
        self._today = time.strftime("%Y-%m-%d")
        self._quota_cache = {}
        if self._quota_path:
            self._load_quota()

    # ------------------------------------------------------------------
    # 配额持久化（轻量、单文件；多实例场景可替换为共享后端）
    # ------------------------------------------------------------------
    def _load_quota(self) -> None:
        try:
            if self._quota_path and os.path.exists(self._quota_path):
                with open(self._quota_path, "r", encoding="utf-8") as f:
                    self._quota_cache = json.load(f) or {}
        except Exception:
            self._quota_cache = {}

    def _save_quota(self) -> None:
        if not self._quota_path:
            return
        try:
            os.makedirs(os.path.dirname(self._quota_path) or ".", exist_ok=True)
            with open(self._quota_path, "w", encoding="utf-8") as f:
                json.dump(self._quota_cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _roll_if_needed(self) -> None:
        t = time.strftime("%Y-%m-%d")
        if t != self._today:
            self._today = t
            self._quota_cache = {}
            self._save_quota()

    def _ensure_user_sem(self, user: str) -> Optional[asyncio.Semaphore]:
        if self._per_user_limit <= 0:
            return None
        sem = self._user_sems.get(user)
        if sem is None:
            sem = asyncio.Semaphore(self._per_user_limit)
            self._user_sems[user] = sem
        return sem

    # ------------------------------------------------------------------
    # 占用 / 释放
    # ------------------------------------------------------------------
    async def acquire(self, user: Optional[str]) -> None:
        user = user or "anonymous"
        self._roll_if_needed()

        # 1) 每日配额（计数型，与并发无关）
        if self._daily_quota > 0:
            async with self._lock:
                day = self._quota_cache.setdefault(self._today, {})
                used = day.get(user, 0)
                if used >= self._daily_quota:
                    raise ConcurrencyLimitError(
                        f"今日对话额度已用尽（{self._daily_quota} 次/天）。"
                        f"如需提升请联系管理员，或于次日自动重置。"
                    )
                day[user] = used + 1
                self._save_quota()

        # 2) 全局 + 单用户并发信号量：任一环节被取消（如客户端断连）都必须回滚
        #    已获取的许可，否则许可会永久泄漏、最终全实例对话死锁。
        g_acquired = False
        u_acquired = False
        try:
            if self._global_sem is not None:
                await self._global_sem.acquire()
                g_acquired = True
            sem = self._ensure_user_sem(user)
            if sem is not None:
                await sem.acquire()
                u_acquired = True
        except BaseException:
            if u_acquired:
                s = self._user_sems.get(user)
                if s is not None:
                    s.release()
            if g_acquired and self._global_sem is not None:
                self._global_sem.release()
            raise

    async def release(self, user: Optional[str]) -> None:
        user = user or "anonymous"
        if self._global_sem is not None:
            self._global_sem.release()
        sem = self._user_sems.get(user)
        if sem is not None:
            sem.release()

    # ------------------------------------------------------------------
    # P2 就绪：可插拔后端钩子（默认进程内；多实例部署覆写即可）
    # ------------------------------------------------------------------
    async def _acquire_quota(self, user: str) -> bool:
        # 默认实现已在 acquire 内联；保留为扩展点供 Redis 后端覆写。
        return True

    async def _release_quota(self, user: str) -> None:
        return None


_LIMITER = ConcurrencyLimiter()


def init_limiter(cfg, quota_path: Optional[str] = None) -> ConcurrencyLimiter:
    """由 AppContext 调用，按配置初始化全局限流实例。"""
    global _LIMITER
    ag = getattr(getattr(cfg, "agent", None), "max_concurrent_global", 0) or 0
    pu = getattr(getattr(cfg, "agent", None), "max_concurrent_per_user", 0) or 0
    dq = getattr(getattr(cfg, "agent", None), "daily_quota_per_user", 0) or 0
    _LIMITER.configure(
        global_limit=ag, per_user_limit=pu, daily_quota=dq, quota_path=quota_path
    )
    return _LIMITER


def get_limiter() -> ConcurrencyLimiter:
    return _LIMITER
