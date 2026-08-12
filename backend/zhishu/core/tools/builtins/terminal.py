"""本地终端工具（本地内网执行，禁止出网）。

安全闸门与定时任务 shell 动作**共用** `core/shellguard`：
  1. 角色门：user 及以上；
  2. 总开关 security.allow_shell；
  3. 高危拒绝清单 + 可执行文件白名单；
  4. 最小化环境变量（剔除密钥）+ 独立进程组 + 超时整组击杀 + POSIX 资源上限。

此前本工具只有角色门：任何 operator 都能用 `cat /etc/shadow`、`env`（读走
ZHISHU_SECRET 与各 Provider Key）或 `curl x | sh` 打穿宿主机。
"""
from __future__ import annotations

from ..base import tool
from .sandbox import sandbox_cwd_for, SANDBOX_ROOT
from .artifacts import snapshot, publish_diff, publish_referenced_paths, append_unique_links
from ...shellguard import check_command, run_guarded


@tool(
    "terminal_run",
    "执行本地 shell 命令（仅内网本机，命令受白名单与高危拦截约束）。",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认30"},
        },
        "required": ["command"],
    },
    toolset="core",
)
async def terminal_run(args: dict, ctx) -> str:
    role = getattr(ctx, "user_role", None)
    if role not in ("admin", "operator", "user"):
        return "[已拦截] 当前角色无权执行终端命令（需 user 及以上）"

    sec = getattr(ctx, "security", None)
    if sec is not None and not getattr(sec, "allow_shell", True):
        return "[已拦截] 系统已关闭本地命令执行（security.allow_shell=false）"

    cmd = (args.get("command") or "").strip()
    if not cmd:
        return "空命令"
    try:
        timeout = int(args.get("timeout", 30))
    except (TypeError, ValueError):
        timeout = 30
    timeout = max(1, min(timeout, 300))

    reason = check_command(
        cmd,
        allowlist=(getattr(sec, "shell_allowlist", None) or None) if sec else None,
        enforce_allowlist=getattr(sec, "shell_enforce_allowlist", True) if sec else True,
    )
    if reason:
        return f"[已拦截] {reason}"

    media = getattr(ctx, "media", None)
    owner = getattr(ctx, "user", "anonymous") or "anonymous"
    cwd = sandbox_cwd_for(owner)
    # 执行前快照工作区，自动发布本次新增/修改的文件为下载链接
    before = snapshot(cwd) if media is not None else {}
    result = await run_guarded(
        cmd, cwd=cwd, timeout=timeout, max_output=8000,
        mem_mb=int(getattr(sec, "shell_mem_limit_mb", 1024) or 0) if sec else 1024,
    )
    if media is not None:
        result += publish_diff(cwd, before, media, owner)
        # 兜底：捕获模型写到 cwd 之外、却把内部绝对路径回显给用户的真实产物
        _ref_text, _refs = publish_referenced_paths(
            result, media, owner,
            media_root=getattr(media, "root", None),
            sandbox_root=SANDBOX_ROOT, out_dir=None,
        )
        if _refs:
            result = append_unique_links(_ref_text, _refs)
    return result
