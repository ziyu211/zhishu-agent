"""智枢智能体 —— 定时任务调度器（对标 Hermes cron，内网合规版）。

特性：
  * 纯 asyncio + SQLite，零外部调度依赖，离线可用。
  * 调度类型：interval（每隔 N 秒/分/时/天）、daily（每天 HH:MM）、cron（5 段表达式）。
  * 任务动作：chat（用 Agent 跑一段提示词，结果落库）、shell（在沙箱内限时执行命令）。
  * 任务定义持久化，重启后自动恢复并续算 next_run。
  * 并发受 max_concurrency 限制；全部异常内部吞掉，单任务失败不影响调度。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from typing import Optional


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse(dt: str) -> datetime:
    return datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# 调度计算：根据类型返回「after 之后的下一次触发时间」
# --------------------------------------------------------------------------
def next_run(schedule_type: str, config: dict, after: Optional[datetime] = None) -> datetime:
    after = after or datetime.now()
    if schedule_type == "interval":
        # 兼容 API/前端传入的复数单位（seconds/minutes/hours/days）
        unit = (config.get("unit") or "hour").rstrip("s")
        every = max(1, int(config.get("every", 1)))
        delta = {"second": timedelta(seconds=every), "minute": timedelta(minutes=every),
                 "hour": timedelta(hours=every), "day": timedelta(days=every)}.get(unit, timedelta(hours=every))
        return after + delta
    if schedule_type == "daily":
        h, m = int(config.get("hour", 9)), int(config.get("minute", 0))
        cand = after.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= after:
            cand += timedelta(days=1)
        return cand
    if schedule_type == "cron":
        return _next_cron(config.get("expr", ""), after)
    # 兜底：1 小时后
    return after + timedelta(hours=1)


def _next_cron(expr: str, after: datetime) -> datetime:
    """极简 cron 匹配（支持 * , - /n），向前扫描至 366 天内首次命中。"""
    parts = expr.split()
    if len(parts) != 5:
        return after + timedelta(hours=1)
    fields = [_parse_field(p, lo, hi) for p, lo, hi in zip(
        parts, (0, 0, 1, 1, 0), (59, 23, 31, 12, 6))]
    cand = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (cand.minute in fields[0] and cand.hour in fields[1] and cand.day in fields[2]
                and cand.month in fields[3] and (cand.weekday() in fields[4] or 7 in fields[4]
                                                  or len(fields[4]) == 0)):
            return cand
        cand += timedelta(minutes=1)
    return after + timedelta(days=1)


def _parse_field(field: str, lo: int, hi: int) -> set:
    out: set = set()
    if field.strip() == "*":
        return set(range(lo, hi + 1))
    for seg in field.split(","):
        if "/" in seg:
            base, step = seg.split("/", 1)
            step = int(step)
        else:
            base, step = seg, 1
        if "-" in base:
            a, b = base.split("-")
            a, b = int(a), int(b)
        elif base == "*":
            a, b = lo, hi
        else:
            a = b = int(base)
        for v in range(a, b + 1, step):
            if lo <= v <= hi:
                out.add(v)
    if hi == 6 and lo == 0:   # 周字段 0-6；兼容 7 表示周日
        pass
    return out


class CronScheduler:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = getattr(cfg.cron, "enabled", True)
        self.data_dir = os.path.join(cfg.server.data_dir, cfg.cron.store_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.db = os.path.join(self.data_dir, "cron.db")
        self._sem = asyncio.Semaphore(max(1, getattr(cfg.cron, "max_concurrency", 2)))
        self._task: Optional[asyncio.Task] = None
        self._init_db()

    def _init_db(self):
        conn = self._conn()
        conn.execute("""CREATE TABLE IF NOT EXISTS cron_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, schedule_type TEXT,
            schedule_config TEXT, action TEXT, payload TEXT, model TEXT,
            owner TEXT, enabled INTEGER DEFAULT 1, last_run TEXT, next_run TEXT,
            created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, started_at TEXT,
            finished_at TEXT, status TEXT, output TEXT)""")
        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(self.db, check_same_thread=False)

    # --------------------- 生命周期 ---------------------
    def start(self):
        if not self.enabled or self._task is not None:
            return
        self._repair_next_run()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _loop(self):
        while True:
            try:
                now = datetime.now()
                for job in self.list_jobs():
                    if not job["enabled"]:
                        continue
                    nr = job["next_run"]
                    if nr and _parse(nr) <= now:
                        asyncio.create_task(self._dispatch(job))
            except Exception:
                pass
            # 自适应休眠：睡到「最近一次到期」或最多 15s；无任务时空转 5s 以快速感知新任务
            try:
                soonest = None
                for job in self.list_jobs():
                    if not job["enabled"] or not job["next_run"]:
                        continue
                    nt = _parse(job["next_run"])
                    if soonest is None or nt < soonest:
                        soonest = nt
                if soonest is not None:
                    wait = max(1, min(15, (soonest - datetime.now()).total_seconds()))
                else:
                    wait = 5
            except Exception:
                wait = 5
            await asyncio.sleep(wait)

    def _repair_next_run(self):
        for job in self.list_jobs():
            if not job["next_run"]:
                nr = next_run(job["schedule_type"], json.loads(job["schedule_config"]))
                self._set_next(job["id"], nr)

    # --------------------- 任务持久化 ---------------------
    def create_job(self, name, schedule_type, schedule_config, action, payload,
                   model=None, owner=None, enabled=1) -> int:
        nr = next_run(schedule_type, schedule_config)
        conn = self._conn()
        cur = conn.execute(
            """INSERT INTO cron_jobs (name, schedule_type, schedule_config, action,
               payload, model, owner, enabled, next_run, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (name, schedule_type, json.dumps(schedule_config), action, payload,
             model, owner, int(enabled), nr.strftime("%Y-%m-%d %H:%M:%S"), _now()))
        conn.commit()
        jid = cur.lastrowid
        conn.close()
        return jid

    def update_job(self, jid, **kw):
        allowed = {"name", "schedule_type", "schedule_config", "action", "payload",
                   "model", "owner", "enabled"}
        sets, vals = [], []
        for k, v in kw.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if k == "schedule_config" else v)
        if not sets:
            return
        conn = self._conn()
        conn.execute(f"UPDATE cron_jobs SET {','.join(sets)} WHERE id=?", vals + [jid])
        conn.commit()
        conn.close()

    def delete_job(self, jid):
        conn = self._conn()
        conn.execute("DELETE FROM cron_jobs WHERE id=?", (jid,))
        conn.execute("DELETE FROM cron_runs WHERE job_id=?", (jid,))
        conn.commit()
        conn.close()

    def set_enabled(self, jid, enabled: bool):
        conn = self._conn()
        conn.execute("UPDATE cron_jobs SET enabled=? WHERE id=?", (int(enabled), jid))
        conn.commit()
        conn.close()

    def list_jobs(self) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT id,name,schedule_type,schedule_config,action,payload,model,owner,"
            "enabled,last_run,next_run,created_at FROM cron_jobs ORDER BY id DESC"
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            out.append(dict(zip(
                ["id", "name", "schedule_type", "schedule_config", "action", "payload",
                 "model", "owner", "enabled", "last_run", "next_run", "created_at"], r)))
            out[-1]["schedule_config"] = json.loads(out[-1]["schedule_config"])
            out[-1]["enabled"] = bool(out[-1]["enabled"])
        return out

    def get_job(self, jid) -> Optional[dict]:
        for j in self.list_jobs():
            if j["id"] == jid:
                return j
        return None

    def history(self, jid, limit: int = 20) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT id,job_id,started_at,finished_at,status,output FROM cron_runs "
            "WHERE job_id=? ORDER BY id DESC LIMIT ?", (jid, limit)).fetchall()
        conn.close()
        return [dict(zip(["id", "job_id", "started_at", "finished_at", "status", "output"], r))
                for r in rows]

    def _set_next(self, jid, dt: datetime):
        conn = self._conn()
        conn.execute("UPDATE cron_jobs SET next_run=? WHERE id=?",
                     (dt.strftime("%Y-%m-%d %H:%M:%S"), jid))
        conn.commit()
        conn.close()

    def _record_run(self, jid, started, status, output):
        conn = self._conn()
        conn.execute(
            "INSERT INTO cron_runs (job_id, started_at, finished_at, status, output) "
            "VALUES (?,?,?,?,?)",
            (jid, started, _now(), status, (output or "")[:4000]))
        conn.commit()
        conn.close()

    # --------------------- 执行 ---------------------
    async def _dispatch(self, job: dict):
        async with self._sem:
            started = _now()
            try:
                if job["action"] == "chat":
                    output = await self._run_chat(job)
                elif job["action"] == "shell":
                    output = await self._run_shell(job)
                else:
                    output = f"未知动作：{job['action']}"
                self._record_run(job["id"], started, "success", output)
            except Exception as e:
                self._record_run(job["id"], started, "error", f"{type(e).__name__}: {e}")
            finally:
                nr = next_run(job["schedule_type"], job["schedule_config"])
                self._set_next(job["id"], nr)
                conn = self._conn()
                conn.execute("UPDATE cron_jobs SET last_run=? WHERE id=?",
                             (started, job["id"]))
                conn.commit()
                conn.close()

    async def _run_chat(self, job: dict) -> str:
        from ..context import get_ctx
        from ..core.agent import Agent
        ctx = get_ctx()
        agent = ctx.build_agent(owner=job.get("owner"))
        model = job.get("model") or ctx.cfg.cron.default_model or ctx.cfg.default_model
        # 多用户隔离：定时任务以任务归属者身份运行；admin 的任务保留 admin 视角
        _owner = job.get("owner")
        _role = None
        _is_admin = False
        try:
            row = ctx.users.get_by_name(_owner) if _owner else None
            if row:
                # sqlite3.Row 没有 .get()：先转 dict 再取角色，否则 AttributeError 被
                # 外层 except 吞掉 → 定时任务永远以「无角色/非管理员」身份运行，
                # 看不到共享(share_with) Provider/模块，表现为「手动正常、定时报错」。
                _role = dict(row).get("role")
                _is_admin = _role == "admin"
        except Exception:
            _is_admin = False
        parts: list[str] = []
        async for ev in agent.run(
            job["payload"], session=f"cron:{job['id']}", model=model,
            owner=_owner, is_admin=_is_admin, user_role=_role,
        ):
            t = ev.get("type")
            if t == "token":
                parts.append(ev.get("text", ""))
            elif t == "error":
                raise RuntimeError(ev.get("message", "对话任务失败"))
            elif t == "done":
                break
        return "".join(parts)[:4000]

    async def _run_shell(self, job: dict) -> str:
        sandbox = os.environ.get("ZHISHU_SANDBOX",
                                 os.path.join(self.cfg.server.data_dir, "sandbox"))
        os.makedirs(sandbox, exist_ok=True)
        proc = await asyncio.create_subprocess_shell(
            job["payload"],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=sandbox,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return "（命令执行超时 300s 已终止）"
        return (out or b"").decode("utf-8", "ignore")[:4000]

    # --------------------- 手动触发 ---------------------
    async def run_now(self, jid) -> str:
        job = self.get_job(jid)
        if not job:
            return "任务不存在"
        started = _now()
        try:
            if job["action"] == "chat":
                output = await self._run_chat(job)
            elif job["action"] == "shell":
                output = await self._run_shell(job)
            else:
                output = f"未知动作：{job['action']}"
            self._record_run(job["id"], started, "success", output)
            return output
        except Exception as e:
            self._record_run(job["id"], started, "error", f"{type(e).__name__}: {e}")
            return f"执行失败：{e}"
