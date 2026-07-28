"""智枢智能体 —— MCP 客户端（对标 Hermes `tools/mcp_tool.py`）。

  把外部 MCP server 的工具注册进同一 ToolRegistry，对模型完全透明。
  支持三种 transport（对标 Hermes mcp_servers 的 stdio / StreamableHTTP / SSE）：
    * stdio —— 子进程 JSON-RPC 2.0，换行分隔（默认，与重构前一致）。
    * http   —— JSON-RPC over HTTP POST（响应为 JSON）。
    * sse    —— JSON-RPC over HTTP，服务端以 SSE（data: 行）推送响应。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Optional

from ..tools import ToolRegistry, Tool, ToolContext


def _make_mcp_handler(client, tool_name: str):
    async def handler(args: dict, ctx: ToolContext) -> str:
        try:
            return await client.call_tool(tool_name, args)
        except Exception as e:
            return f"[MCP 工具错误] {tool_name}: {e}"
    return handler


class MCPClient:
    def __init__(self, name: str, config: dict, data_dir: Optional[str] = None):
        self.name = name
        self.config = config
        self.data_dir = data_dir
        self.transport = (config.get("transport") or "stdio").lower()
        self.proc = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._pending: dict = {}
        self._reader_task = None
        self._http_session = None
        self.tools: list = []
        self.error: Optional[str] = None

    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if self.transport == "stdio":
            await self._connect_stdio()
        else:
            await self._connect_http()
        try:
            res = await self._request("tools/list", {})
            self.tools = res.get("tools", []) if isinstance(res, dict) else []
            self.error = None
        except Exception as e:
            self.tools = []
            self.error = f"工具列表获取失败: {e}"

    async def _connect_stdio(self) -> None:
        cmd = self.config.get("command")
        # 双环境自适应：command 为 "auto" 时，使用运行后端自身的 Python 解释器
        # （Docker 容器内即容器内 python；Windows 直跑即 venv python），避免把
        # 容器路径（/app/...）或宿主路径写死进配置导致另一环境 [Errno 2]。
        if not cmd or cmd == "auto":
            cmd = sys.executable
        args = list(self.config.get("args") or [])
        # 双环境自适应：args 中的相对路径视为相对于 data 目录，在运行时解析为
        # 绝对路径，使同一份 mcp.json 在 Docker 与 Windows 下都能正确加载脚本。
        if self.data_dir:
            resolved = []
            for a in args:
                cand = os.path.join(self.data_dir, a)
                if not os.path.isabs(a) and os.path.isfile(cand):
                    resolved.append(os.path.abspath(cand))
                else:
                    resolved.append(a)
            args = resolved
        env = dict(os.environ)
        env.update(self.config.get("env") or {})
        self.proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "zhishu", "version": "1.0.0"},
        })
        await self._notify("notifications/initialized", {})

    async def _connect_http(self) -> None:
        import aiohttp

        url = self.config.get("url")
        if not url:
            raise RuntimeError("MCP(SSE/HTTP) 配置缺少 url")
        self._http_session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"},
        )
        try:
            await self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "zhishu", "version": "1.0.0"},
            })
            await self._notify("notifications/initialized", {})
        except Exception as e:
            # 某些 SSE 服务器不要求 initialize 响应，忽略
            self.error = f"initialize 警告: {e}"

    # ------------------------------------------------------------------
    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line = line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue
                fut = self._pending.pop(mid, None)
                if fut and not fut.done():
                    fut.set_result(msg)
        except Exception:
            pass

    async def _send(self, method: str, params: Any, is_notification: bool = False) -> Any:
        self._req_id += 1
        rid = self._req_id
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            payload["id"] = rid
        if params is not None:
            payload["params"] = params
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        if self.transport == "stdio":
            assert self.proc and self.proc.stdin
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()
        else:
            assert self._http_session is not None
            await self._http_session.post(self.config["url"], data=data)
        return rid

    async def _request(self, method: str, params: Any, timeout: float = 20.0) -> Any:
        async with self._lock:
            rid = await self._send(method, params)
            if self.transport == "stdio":
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._pending[rid] = fut
                try:
                    msg = await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError:
                    self._pending.pop(rid, None)
                    raise RuntimeError(f"MCP 请求超时: {method}")
                if "error" in msg:
                    raise RuntimeError(f"MCP 错误: {msg['error']}")
                return msg.get("result", {})
            else:
                # HTTP/SSE：POST 后读取匹配 id 的响应（JSON 或 SSE data:）
                resp = await self._http_recv(rid, timeout=timeout)
                if "error" in resp:
                    raise RuntimeError(f"MCP 错误: {resp['error']}")
                return resp.get("result", {})

    async def _http_recv(self, rid: int, timeout: float = 20.0) -> dict:
        assert self._http_session is not None
        async with self._http_session.post(
            self.config["url"],
            json={"jsonrpc": "2.0", "id": rid, "method": "ping"},
        ) as r:
            ct = r.headers.get("Content-Type", "")
            if "text/event-stream" in ct:
                async for line in r.content:
                    s = line.decode("utf-8", "replace").strip()
                    if s.startswith("data:"):
                        s = s[5:].strip()
                        try:
                            msg = json.loads(s)
                        except Exception:
                            continue
                        if msg.get("id") == rid:
                            return msg
            else:
                msg = await r.json()
                if msg.get("id") == rid:
                    return msg
        raise RuntimeError("MCP(HTTP) 未收到响应")

    async def _notify(self, method: str, params: Any) -> None:
        async with self._lock:
            await self._send(method, params, is_notification=True)

    async def call_tool(self, tool_name: str, arguments: dict, timeout: float = 60.0) -> str:
        if self.transport == "stdio":
            async with self._lock:
                rid = await self._send("tools/call", {"name": tool_name, "arguments": arguments or {}})
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._pending[rid] = fut
                try:
                    msg = await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError:
                    self._pending.pop(rid, None)
                    raise RuntimeError("MCP 工具调用超时")
                if "error" in msg:
                    raise RuntimeError(f"MCP 工具错误: {msg['error']}")
                result = msg.get("result", {})
                texts = []
                for c in result.get("content", []):
                    if isinstance(c, dict) and c.get("type") == "text":
                        texts.append(c.get("text", ""))
                return "\n".join(texts)
        else:
            res = await self._request("tools/call", {"name": tool_name, "arguments": arguments or {}}, timeout=timeout)
            texts = []
            for c in res.get("content", []):
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text", ""))
            return "\n".join(texts)

    async def close(self) -> None:
        try:
            if self.proc and self.proc.returncode is None:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
        except Exception:
            try:
                if self.proc:
                    self.proc.kill()
            except Exception:
                pass
        if self._reader_task:
            self._reader_task.cancel()
        if self._http_session:
            await self._http_session.close()
