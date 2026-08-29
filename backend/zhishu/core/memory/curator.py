"""智枢技能库管家（Curator）—— 对标 Hermes ``agent/curator.py`` 的「闲置剪枝」。

设计取舍（与 Hermes 对齐）：
  * 确定性「闲置剪枝」常开（``curator_enabled`` 默认 True）：仅处理**自动沉淀**的
    技能（``module.json`` 中 ``created_by=="agent"`` 且 ``auto_generated==True``），
    按最近活跃时间戳（``last_used`` 优先，缺则 ``created_at``）迁移生命周期状态。
  * 从不删除 —— 只把长期零使用的技能归档到 ``skills/.archive/<name>/``（可经
    learning-graph / 导出恢复），与 Hermes「archive is recoverable」一致。
  * 用户自建技能（``created_by=="user"``）、Builtin（无 ``created_by``）、
    被 ``pinned`` 的技能一律跳过。
  * LLM 合并（consolidation / umbrella-building）本 P1 不做（Hermes 默认亦 OFF，
    且消耗辅助模型成本），仅做零成本的确定性剪枝。
  * 触发方式：空闲触发（无 cron 守护）——每轮对话成功结束后，若距上次巡检已超
    ``interval`` 且距上次任意运行已空闲 ``min_idle`` 小时，则异步跑一次
    ``apply_curator``。状态持久化在 ``<data_dir>/.curator_state.json``。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("zhishu.curator")


# ---------------------------------------------------------------------------
# 时间解析 / 状态持久化
# ---------------------------------------------------------------------------

def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:  # 兼容 ISO 含 Z
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path(data_dir: str) -> str:
    return os.path.join(data_dir, ".curator_state.json")


def _default_state() -> Dict[str, Any]:
    return {
        "last_run_at": None,
        "last_run_summary": None,
        "run_count": 0,
        "last_counts": {},
    }


def load_state(data_dir: str) -> Dict[str, Any]:
    p = _state_path(data_dir)
    if os.path.isfile(p):
        try:
            data = json.loads(open(p, encoding="utf-8").read())
            if isinstance(data, dict):
                base = _default_state()
                base.update({k: v for k, v in data.items() if k in base})
                return base
        except Exception:
            pass
    return _default_state()


def save_state(data_dir: str, state: Dict[str, Any]) -> None:
    try:
        with open(_state_path(data_dir), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:  # pragma: no cover — best-effort
        logger.debug("curator: failed to save state: %s", e)


# ---------------------------------------------------------------------------
# 静态闸门：是否应触发一次巡检
# ---------------------------------------------------------------------------

def should_run(cfg_agent, data_dir: str, now: Optional[datetime] = None) -> bool:
    """仅做静态闸门（enabled + interval）。首跑推迟一个 interval 并种下 last_run_at。"""
    if not getattr(cfg_agent, "curator_enabled", True):
        return False
    if now is None:
        now = _now()
    state = load_state(data_dir)
    last = _parse_ts(state.get("last_run_at"))
    if last is None:
        # 首跑也推迟一个 interval，避免 fresh 实例立即剪枝
        try:
            st = load_state(data_dir)
            st["last_run_at"] = now.isoformat()
            st["last_run_summary"] = "deferred first run — will prune after one interval"
            save_state(data_dir, st)
        except Exception:
            pass
        return False
    interval = timedelta(hours=getattr(cfg_agent, "curator_interval_hours", 24 * 7))
    return (now - last) >= interval


# ---------------------------------------------------------------------------
# 确定性剪枝（纯函数，无 LLM）
# ---------------------------------------------------------------------------

def _read_meta(skills_base: str, name: str) -> Dict[str, Any]:
    fp = os.path.join(skills_base, name, "module.json")
    if os.path.isfile(fp):
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_meta(skills_base: str, name: str, meta: Dict[str, Any]) -> None:
    d = os.path.join(skills_base, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def apply_curator(cfg_agent, data_dir: str, owner: Optional[str] = None,
                  now: Optional[datetime] = None) -> Dict[str, int]:
    """确定性闲置剪枝。返回各状态变更计数。

    ``owner`` 非空时只处理该用户归属的自动技能（多租户安全：一次闲置巡检不跨界
    归档他人技能）；``owner`` 为空（系统级触发）则扫描全部自动技能。
    """
    if now is None:
        now = _now()
    skills_base = os.path.join(data_dir, "skills")
    archive_base = os.path.join(skills_base, ".archive")
    if not os.path.isdir(skills_base):
        return {"checked": 0, "marked_stale": 0, "archived": 0, "reactivated": 0}

    stale_cut = now - timedelta(days=getattr(cfg_agent, "curator_stale_after_days", 30))
    archive_cut = now - timedelta(days=getattr(cfg_agent, "curator_archive_after_days", 90))

    counts = {"checked": 0, "marked_stale": 0, "archived": 0, "reactivated": 0}
    for name in sorted(os.listdir(skills_base)):
        if name.startswith("."):
            continue
        d = os.path.join(skills_base, name)
        if not os.path.isdir(d):
            continue
        meta = _read_meta(skills_base, name)
        counts["checked"] += 1
        # 仅自动沉淀技能（created_by=="agent" 且 auto_generated）
        if meta.get("created_by") != "agent" or not meta.get("auto_generated"):
            continue
        # 多用户隔离：owner 限定归属
        if owner and (meta.get("owner") or None) not in (None, owner):
            continue
        # pinned 跳过
        if meta.get("pinned"):
            continue

        anchor = (_parse_ts(meta.get("last_used"))
                  or _parse_ts(meta.get("created_at"))
                  or now)
        use_count = int(meta.get("use_count") or 0)
        state_cur = meta.get("state", "active")

        # 零使用技能给予宽限期：未超过 stale 窗口则整体不动
        if use_count == 0 and anchor > stale_cut:
            if state_cur == "stale":
                meta["state"] = "active"
                _write_meta(skills_base, name, meta)
                counts["reactivated"] += 1
            continue

        if anchor <= archive_cut and state_cur != "archived":
            dest = os.path.join(archive_base, name)
            if os.path.exists(dest):
                continue
            try:
                os.makedirs(archive_base, exist_ok=True)
                shutil.move(d, dest)  # 归档（可恢复，绝不删除）
                counts["archived"] += 1
            except Exception as e:
                logger.debug("curator: archive %s failed: %s", name, e)
        elif anchor <= stale_cut and state_cur == "active":
            meta["state"] = "stale"
            _write_meta(skills_base, name, meta)
            counts["marked_stale"] += 1
        elif anchor > stale_cut and state_cur == "stale":
            meta["state"] = "active"
            _write_meta(skills_base, name, meta)
            counts["reactivated"] += 1
    return counts


# ---------------------------------------------------------------------------
# 空闲触发入口（在 agent 回合结束后以 create_task 调用）
# ---------------------------------------------------------------------------

async def maybe_run_curator(cfg, owner: Optional[str] = None) -> Optional[Dict[str, int]]:
    """空闲触发的巡检入口。仅做闸门检查，真正剪枝在 apply_curator（线程池执行）。
    异常全部吞掉，绝不注入任何可观测故障到主对话。"""
    try:
        ac = getattr(cfg, "agent", None)
        if not ac or not getattr(ac, "curator_enabled", True):
            return None
        data_dir = cfg.server.data_dir
        now = _now()
        # idle 闸门：距上次任意运行需已空闲 min_idle 小时
        state = load_state(data_dir)
        last_any = _parse_ts(state.get("last_run_at"))
        if last_any is not None and (now - last_any) < timedelta(
                hours=getattr(ac, "curator_min_idle_hours", 2.0)):
            return None
        if not should_run(ac, data_dir, now):
            return None
        counts = await asyncio.to_thread(apply_curator, ac, data_dir, owner, now)
        st = load_state(data_dir)
        st["last_run_at"] = now.isoformat()
        st["last_run_summary"] = (
            f"marked_stale={counts['marked_stale']} archived={counts['archived']} "
            f"reactivated={counts['reactivated']} checked={counts['checked']}"
        )
        st["run_count"] = int(st.get("run_count", 0)) + 1
        st["last_counts"] = counts
        save_state(data_dir, st)
        return counts
    except Exception as e:
        logger.debug("curator: maybe_run_curator error: %s", e)
        return None
