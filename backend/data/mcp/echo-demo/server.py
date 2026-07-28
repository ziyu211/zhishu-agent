#!/usr/bin/env python3
"""智枢 MCP 演示服务（纯标准库，零依赖）。

实现一个最小可用的 MCP stdio 服务器：
  * 工具 echo：回显传入的 message。
仅用于演示「智枢 ↔ MCP 服务器」连通，可放心删除。
"""
import json
import sys


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-demo", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "回显输入的文本",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"message": {"type": "string", "description": "要回显的内容"}},
                            "required": ["message"],
                        },
                    }
                ]
            },
        }
    if method == "tools/call":
        name = msg.get("params", {}).get("name")
        args = msg.get("params", {}).get("arguments", {}) or {}
        if name == "echo":
            text = args.get("message", "")
            return {
                "jsonrpc": "2.0", "id": mid,
                "result": {
                    "content": [{"type": "text", "text": f"[echo] {text}"}],
                    "isError": False,
                },
            }
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True},
        }
    # 未知方法
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        resp = handle(msg)
        if resp is not None:
            send(resp)


if __name__ == "__main__":
    main()
