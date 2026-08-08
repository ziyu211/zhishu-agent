"""智枢智能体 —— 插件层（对标 Hermes `agent/plugins.py`）。

插件以「目录即模块」管理：data/plugins/<name>/module.json 记录 tools 列表，
每个 tool 描述一个 shell / http 自定义工具。register_plugin_tools() 在启动时把
全部已启用插件的工具注册进同一 ToolRegistry，对模型完全透明
（命名 plugin__<插件>__<工具>）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

from ..tools import ToolRegistry, Tool
from .runtime import load_state, read_meta, DISABLED_KEY

logger = logging.getLogger("zhishu.plugins")


def _substitute(template: str, args: dict) -> str:
    if not template:
        return ""
    def repl(m):
        return str(args.get(m.group(1), ""))
    return re.sub(r"\{\{(\w+)\}\}", repl, template)


def _plugin_schema(plugin: str, t: dict) -> dict:
    props = {}
    required = []
    for p in (t.get("parameters") or []):
        nm = p.get("name")
        if not nm:
            continue
        props[nm] = {"type": p.get("type", "string"), "description": p.get("description", "")}
        if p.get("required"):
            required.append(nm)
    return {
        "type": "function",
        "function": {
            "name": f"plugin__{plugin}__{t.get('name', '')}",
            "description": t.get("description", ""),
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def _make_plugin_handler(plugin: str, t: dict):
    async def handler(args: dict, ctx) -> str:
        typ = t.get("type", "shell")
        # 出网隔离开关（http 类插件需显式放行）
        if typ == "http" and ctx.security and not ctx.security.outbound_allow:
            return "[已拦截] 当前为内网隔离模式，禁止 HTTP 插件访问外部网络。"
        try:
            if typ == "http":
                return await _run_http(t, args, getattr(ctx.security, "allow_private_fetch", False))
            return await _run_shell(t, args)
        except Exception as e:
            return f"[插件工具错误] {plugin}/{t.get('name')}: {e}"
    return handler


async def _run_shell(t: dict, args: dict) -> str:
    cmd = t.get("command", "")
    cargs = list(t.get("args") or [])
    # 无论 command_is_template 是否设置，参数模板 {{x}} 都应按调用参数替换
    # （修复：此前仅当 command_is_template 为真才替换，导致 parse_* 等插件
    #  收到字面量 "{{path}}" 而非真实路径而失败）。
    cmd = _substitute(cmd, args)
    cargs = [_substitute(a, args) for a in cargs]
    if not cmd:
        return "[插件错误] 未配置 command"
    proc = await asyncio.create_subprocess_exec(
        cmd, *cargs,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    text = (out or b"").decode("utf-8", "replace")
    if proc.returncode not in (0, None) and not text and err:
        text = (err or b"").decode("utf-8", "replace")
    return text[:8000]


async def _run_http(t: dict, args: dict, allow_private: bool = False) -> str:
    from ..ssrf import guard_url
    import httpx
    url = _substitute(t.get("url", ""), args)
    if not guard_url(url, allow_private=allow_private):
        return ("[已拦截] 目标地址为内网/私有地址，出于 SSRF 防护已拒绝"
                "（如需放开请配置 security.allow_private_fetch=true）。")
    method = (t.get("method") or "POST").upper()
    headers = dict(t.get("headers") or {})
    body = _substitute(t.get("body_template") or "", args)
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.request(method, url, headers=headers, content=body or None)
        return r.text[:8000]


def _data_dir() -> str:
    from ...context import get_ctx
    return get_ctx().cfg.server.data_dir


def register_plugin_tools() -> int:
    """扫描已启用插件，注册其自定义工具到 ToolRegistry。返回注册数量。"""
    ToolRegistry.clear_prefix("plugin__")
    root = os.path.join(_data_dir(), "plugins")
    if not os.path.isdir(root):
        return 0
    state = load_state()
    disabled = set(state.get(DISABLED_KEY["plugins"], []))
    count = 0
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d) or name in disabled:
            continue
        # 单插件注册失败隔离（闭环修复 Task #399）：一个坏插件（元信息缺字段 / handler
        # 构造异常）不得拖垮其余插件注册，也不得让上层拿到「假成功」——记录日志后可观测。
        try:
            meta = read_meta("plugins", name)
            if meta.get("enabled") is False:
                continue
            for t in (meta.get("tools") or []):
                tname = t.get("name")
                if not tname:
                    continue
                spec = _plugin_schema(name, t)
                fn = spec["function"]
                ToolRegistry.register(Tool(
                    name=fn["name"],
                    description=fn["description"],
                    parameters=fn["parameters"],
                    handler=_make_plugin_handler(name, t),
                    toolset="plugin",
                ))
                count += 1
        except Exception as e:
            logger.error("[plugins] 注册插件 %s 失败，已跳过（其余插件不受影响）：%s", name, e)
    return count
