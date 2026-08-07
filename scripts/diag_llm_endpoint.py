# -*- coding: utf-8 -*-
"""诊断 OpenAI 兼容模型端点为何返回 400（Py2/Py3 双兼容）。

在真实环境（默认 python 为 Python2 的机器）直接运行：
    python diag_llm_endpoint.py http://32.36.26.39:8010 [API_KEY] [MODEL]

会依次探测：
  1) GET  /v1/models            —— 看服务端有哪些可用 model 名
  2) POST /v1/chat/completions  —— 不带 tools（最小请求）
  3) POST /v1/chat/completions  —— 带 tools + tool_choice（复现智枢 agent 的真实请求）

把三段的状态码 + 响应体原文贴回，即可定位 400 根因。
"""
from __future__ import print_function

import sys
import json

# ---- Py2/Py3 兼容的 HTTP 客户端 ----
try:
    # Python 3
    import urllib.request as ureq
    import urllib.error as uerr
    PY3 = True
except ImportError:
    # Python 2
    import urllib2 as ureq
    import urllib2 as uerr
    PY3 = False


def _to_bytes(s):
    if isinstance(s, bytes):
        return s
    try:
        return s.encode("utf-8")
    except Exception:
        return s


def _http(method, url, headers, body):
    """返回 (status_int, header_dict, text_str)。"""
    if PY3:
        req = ureq.Request(url, data=_to_bytes(body) if body is not None else None,
                          method=method)
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            resp = ureq.urlopen(req, timeout=20)
            status = resp.getcode()
            raw = resp.read()
            h = dict(resp.info())
        except uerr.HTTPError as e:
            status = e.code
            raw = e.read()
            h = dict(e.info())
        except uerr.URLError as e:
            return (-1, {}, u"连接失败: %s" % repr(e.reason))
    else:
        req = ureq.Request(url, data=_to_bytes(body) if body is not None else None)
        req.get_method = lambda: method
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            resp = ureq.urlopen(req, timeout=20)
            status = resp.getcode()
            raw = resp.read()
            h = dict(resp.info())
        except uerr.HTTPError as e:
            status = e.code
            raw = e.read()
            h = dict(e.info())
        except uerr.URLError as e:
            return (-1, {}, u"连接失败: %s" % repr(e.reason))
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        text = repr(raw)
    ctype = h.get("Content-Type") or h.get("content-type") or ""
    return (status, {"content-type": ctype}, text)


def _show(title, method, url, body, api_key):
    hdr = {"Content-Type": "application/json"}
    if api_key:
        hdr["Authorization"] = "Bearer " + api_key
    print("=" * 60)
    print(title)
    print("%s %s" % (method, url))
    if body is not None:
        print("--- request body ---")
        print(body)
    status, h, text = _http(method, url, hdr, body)
    print("--- response ---")
    print("HTTP %s  content-type=%s" % (status, h.get("content-type", "")))
    # 截断超长响应，保留前 800 字符
    if len(text) > 800:
        text = text[:800] + u" ...[truncated]"
    print(text)
    print("")
    return status


def main():
    if len(sys.argv) < 2:
        print("用法: python diag_llm_endpoint.py <BASE_URL> [API_KEY] [MODEL]")
        print("示例: python diag_llm_endpoint.py http://32.36.26.39:8010  sk-xxx  qwen2.5")
        sys.exit(1)
    base = sys.argv[1].rstrip("/")
    api_key = sys.argv[2] if len(sys.argv) > 2 else ""
    model = sys.argv[3] if len(sys.argv) > 3 else "local-model"

    print("BASE_URL = %s" % base)
    print("API_KEY  = %s" % ("<set>" if api_key else "<empty>"))
    print("MODEL    = %s" % model)
    print("")

    # 1) 列出可用模型
    _show("[1] GET /v1/models", "GET", base + "/v1/models", None, api_key)

    # 2) 最小 chat（不带 tools）
    min_body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "你好，请用一个字回答：在"}],
        "max_tokens": 16,
        "stream": False,
    })
    _show("[2] POST /v1/chat/completions  (不带 tools)", "POST",
          base + "/v1/chat/completions", min_body, api_key)

    # 3) 带 tools（复现智枢 agent 请求，最常见 400 来源）
    tools_body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "查一下李龙的积分"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "lookup_person",
                "description": "按姓名查询人员信息",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        }],
        "tool_choice": "auto",
        "max_tokens": 256,
        "stream": False,
    })
    _show("[3] POST /v1/chat/completions  (带 tools+tool_choice)", "POST",
          base + "/v1/chat/completions", tools_body, api_key)

    print("=" * 60)
    print("判读提示：")
    print("  - 若 [2] 200 但 [3] 400 -> 服务端不支持 tools/函数调用，需换支持 function calling 的模型")
    print("  - 若 [2] 也 400 且提示 model 不存在 -> MODEL 名不对，用 [1] 列出的真实 model 名")
    print("  - 若 [1] 也失败 -> base_url / 网络 / 鉴权问题")


if __name__ == "__main__":
    main()
