"""受限工作区（沙箱）根与按 owner 隔离的子目录。

历史：code_exec / terminal_run / cron shell 动作曾共用同一个平铺的 `data/sandbox`
作为 cwd。虽然产物落盘（/media/<owner>/...）已按 owner 隔离，但共享 cwd 意味着
User B 的代码若读取 User A 残留在共享沙箱的文件，就会串号，存在数据卫生风险。

本模块把「共享根 + per-owner 子目录」收敛到一处：
  * SANDBOX_ROOT           —— 共享根（保留，向后兼容已由 __init__ 重导的引用）。
  * sandbox_cwd_for(owner) —— 返回该 owner 专属的沙箱子目录（已 makedirs）。
    owner 经过白名单转义，杜绝穿越（../）与跨用户共享同一目录。

工具只在此范围内写文件；模型拿到的永远是 /media/... 下载链接，绝不暴露真实路径。
"""
from __future__ import annotations

import os
import re

# 受限工作区根目录（工具只能在此范围内写文件，防止越权读内网其它文件）。
# 该目录仅作内部执行 cwd 使用，绝不暴露给模型。
SANDBOX_ROOT = os.environ.get("ZHISHU_SANDBOX", "data/sandbox")
os.makedirs(SANDBOX_ROOT, exist_ok=True)

# owner 转义：仅允许常见安全字符，其余一律替换为下划线，杜绝路径穿越与跨用户冲突。
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_owner(owner: str | None) -> str:
    """把任意 owner 字符串转义为目录安全的片段。

    - None / 空 → "anonymous"；
    - 仅保留 A-Za-z0-9_.-，其余（含 / \\ @ 空格等）替换为 _；
    - 禁止以 "." 开头（避免隐藏目录 / 被当作路径段）；
    - 转义后为空 → "anonymous"。
    """
    o = (owner or "").strip()
    if not o:
        return "anonymous"
    o = _SAFE.sub("_", o)
    if o.startswith("."):
        o = "_" + o[1:]
    if not o:
        return "anonymous"
    return o


def sandbox_cwd_for(owner: str | None, base: str | None = None) -> str:
    """返回 owner 专属的沙箱子目录（绝对路径，已确保存在）。

    base 缺省取 SANDBOX_ROOT；cron 在 ZHISHU_SANDBOX 未设时历史默认用
    <data_dir>/sandbox，可显式传入 base 以保留该行为。
    """
    root = base if base is not None else SANDBOX_ROOT
    root = os.path.abspath(root)
    cwd = os.path.join(root, _safe_owner(owner))
    os.makedirs(cwd, exist_ok=True)
    return cwd
