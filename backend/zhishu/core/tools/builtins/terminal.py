"""沙箱终端工具（本地内网执行，禁止出网）。"""
from __future__ import annotations

import asyncio

from ..base import tool
from . import SANDBOX_ROOT


@tool(
    "terminal_run",
    "在隔离沙箱中执行本地 shell 命令（仅内网本机，禁止访问外部网络）。",
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
    cmd = args.get("command", "")
    timeout = int(args.get("timeout", 30))
    if not cmd.strip():
        return "空命令"
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=SANDBOX_ROOT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return "[超时] 命令执行超过 %d 秒已被终止" % timeout
    # 兼容 Windows GBK 输出
    raw = out or b""
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        try:
            text = raw.decode("gbk", errors="ignore")
        except (UnicodeDecodeError, AttributeError):
            text = raw.decode("latin-1", errors="ignore")
    return text.strip()
