"""OpenAI 兼容服务端网关 —— 真实 HTTP 全链路冒烟测试（不依赖外部 LLM / 网络）。

用 FastAPI TestClient 真正走 HTTP 中间件 → 鉴权(require_auth) → 并发限流器 →
/v1/chat/completions、/v1/models 路由 → SSE 序列化 全链路。LLM 用 FakeLLM 打桩。

覆盖：
  A. 鉴权闸门：无 token 请求 /v1/chat/completions → 401
  B. /v1/models 返回 OpenAI 标准 shape（object=list，data[].id 含 provider/）
  C. 非流式 /v1/chat/completions → OpenAI chat.completion dict（choices[0].message.content）
  D. 流式 /v1/chat/completions → SSE 含 role delta、content delta、finish_reason=stop、[DONE]
  E. 工具调用：上游 tool_calls 哨兵被正确翻译回 OpenAI delta.tool_calls

运行：python tests/test_openai_gateway.py
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 本地开发默认密钥放行（代码侧硬闸门会在 enable_auth + 默认密钥时拒绝启动）
os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.config import ZhishuConfig, ProviderConfig
from zhishu.context import get_ctx
from zhishu.main import create_app
from zhishu.core.security import Crypto
from zhishu.api import openai_gw as gw_mod
from fastapi.testclient import TestClient

REPLY_MARK = "【网关冒烟回复】"


class FakeLLM:
    """取代真实模型：chat 返回 OpenAI 原生 dict；stream 产出文本 + 可选 tool_calls 哨兵。"""
    def __init__(self, cfg, api_mode=None):
        self.cfg = cfg
        self.api_mode = api_mode

    async def chat(self, messages, model=None, tools=None, max_tokens=None,
                   tool_choice=None, **_kw):
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": f"{REPLY_MARK}你好，我是智枢。",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }]
        }

    async def stream(self, messages, model=None, tools=None, temperature=0.7,
                     max_tokens=2048, prefer=None, **_kw):
        yield "你好，"
        yield "这是网关流式回复。"
        if tools:
            tc = [{
                "id": "call_1", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"北京"}'},
            }]
            yield f"\u0000TOOLCALL\u0000{json.dumps(tc, ensure_ascii=False)}\u0000"


class FailingLLM:
    """模拟上游异常：rate_limit 抛含 429 的 RuntimeError；auth 抛含 API Key 的异常。"""
    def __init__(self, cfg, api_mode=None, kind: str = "rate"):
        self.cfg = cfg
        self.api_mode = api_mode
        self.kind = kind

    async def chat(self, *a, **k):
        if self.kind == "rate":
            raise RuntimeError("模型服务持续限流（HTTP 429），已重试 3 次仍失败。")
        raise RuntimeError("所有已配置的 LLM Provider 均缺少 API Key 或不可达：agnes（https://x）。")

    async def stream(self, *a, **k):
        if self.kind == "rate":
            raise RuntimeError("模型服务持续限流（HTTP 429），已重试 3 次仍失败。")
        raise RuntimeError("所有已配置的 LLM Provider 均缺少 API Key 或不可达：agnes（https://x）。")


def _build_cfg(tmp: str) -> ZhishuConfig:
    cfg = ZhishuConfig()
    cfg.server.data_dir = tmp
    cfg.security.enable_sm = False
    cfg.security.enable_auth = True
    cfg.security.secret = "change-me-zhishu-secret"
    cfg.security.enable_audit = True
    cfg.providers = {
        "demo": ProviderConfig(name="demo", label="Demo", base_url="http://demo",
                               models=["demo-model"], enabled=True,
                               owner="", shared=False),
    }
    return cfg


def _mint_token(secret: str, user: str = "admin", role: str = "admin",
               ttl: int = 86400 * 7) -> str:
    payload = json.dumps({"u": user, "r": role, "exp": int(time.time()) + ttl})
    sig = Crypto(False).sign(secret, payload)
    return f"{payload}.{sig}"


def _parse_sse(text: str) -> list:
    events = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        try:
            events.append(json.loads(payload))
        except Exception:
            pass
    return events


def test_gateway_auth_required():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        gw_mod.LLMClient = FakeLLM
        app = create_app(cfg)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions",
                               json={"model": "demo/demo-model", "messages": [{"role": "user", "content": "hi"}]})
            assert resp.status_code == 401, f"期望 401，实际 {resp.status_code}: {resp.text[:200]}"
    print("  [A] 未带 token 请求 /v1/chat/completions → 401（鉴权闸门生效）  ✓")


def test_gateway_models_list():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        gw_mod.LLMClient = FakeLLM
        app = create_app(cfg)
        token = _mint_token(cfg.security.secret)
        with TestClient(app) as client:
            resp = client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text[:200]}"
            body = resp.json()
            assert body.get("object") == "list", f"object 应为 list: {body}"
            ids = [m["id"] for m in body.get("data", [])]
            assert "demo/demo-model" in ids, f"应包含 demo/demo-model: {ids}"
            assert all(m["object"] == "model" for m in body["data"]), "data[].object 应为 model"
    print("  [B] /v1/models 返回 OpenAI 标准列表（含 demo/demo-model）  ✓")


def test_gateway_chat_nonstream():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        gw_mod.LLMClient = FakeLLM
        app = create_app(cfg)
        token = _mint_token(cfg.security.secret)
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "demo/demo-model",
                      "messages": [{"role": "user", "content": "你好"}],
                      "stream": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text[:300]}"
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            assert REPLY_MARK in content, f"回复未出现标记: {content}"
            assert body["model"] == "demo/demo-model", f"model 应回显请求 id: {body.get('model')}"
            assert body["object"] == "chat.completion"
    print("  [C] 非流式 /v1/chat/completions → OpenAI chat.completion（content + 回显 model）  ✓")


def test_gateway_chat_stream():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        gw_mod.LLMClient = FakeLLM
        app = create_app(cfg)
        token = _mint_token(cfg.security.secret)
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "demo/demo-model",
                      "messages": [{"role": "user", "content": "你好"}],
                      "stream": True},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text[:300]}"
            events = _parse_sse(resp.text)
            # 首包应为 role=assistant 声明
            assert any(e.get("choices", [{}])[0].get("delta", {}).get("role") == "assistant"
                       for e in events), f"缺少 assistant role 首包: {events[:2]}"
            merged = "".join(
                e["choices"][0]["delta"].get("content", "")
                for e in events if e.get("choices") and e["choices"][0].get("delta", {}).get("content")
            )
            assert "网关流式回复" in merged, f"流式内容未拼接出标记: {merged}"
            assert any(e.get("choices", [{}])[0].get("finish_reason") == "stop" for e in events), \
                "缺少 finish_reason=stop"
            assert any(e == "[DONE]" or (isinstance(e, dict) and e.get("choices", [{}])[0].get("finish_reason") == "stop")
                       for e in events), "应以 stop + [DONE] 收尾"
            assert "data: [DONE]" in resp.text, "流应以 data: [DONE] 结束"
    print("  [D] 流式 /v1/chat/completions → SSE（role/content delta + stop + [DONE]）  ✓")


def test_gateway_toolcall_translation():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        gw_mod.LLMClient = FakeLLM
        app = create_app(cfg)
        token = _mint_token(cfg.security.secret)
        tools = [{"type": "function", "function": {"name": "get_weather",
                  "description": "x", "parameters": {"type": "object", "properties": {}}}}]
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "demo/demo-model",
                      "messages": [{"role": "user", "content": "北京天气"}],
                      "tools": tools, "stream": True},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text[:300]}"
            events = _parse_sse(resp.text)
            saw = False
            for e in events:
                if e == "[DONE]" or not isinstance(e, dict):
                    continue
                delta = e.get("choices", [{}])[0].get("delta", {})
                if delta.get("tool_calls"):
                    saw = True
                    name = delta["tool_calls"][0]["function"]["name"]
                    assert name == "get_weather", f"工具名应为 get_weather: {name}"
            assert saw, f"未出现翻译后的 tool_calls delta: {events}"
    print("  [E] 工具调用哨兵 → OpenAI delta.tool_calls（name=get_weather）  ✓")


@contextlib.contextmanager
def _tmpdir():
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    try:
        test_gateway_auth_required()
        test_gateway_models_list()
        test_gateway_chat_nonstream()
        test_gateway_chat_stream()
        test_gateway_toolcall_translation()
        test_gateway_upstream_429_mapped()
        test_gateway_upstream_auth_mapped()
    except AssertionError as e:
        print("FAILED:", e)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print("ERROR:", repr(e))
        sys.exit(2)
    print("\nALL OPENAI GATEWAY TESTS PASSED")


def test_gateway_upstream_429_mapped():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        gw_mod.LLMClient = lambda c, m: FailingLLM(c, m, kind="rate")
        app = create_app(cfg)
        token = _mint_token(cfg.security.secret)
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "demo/demo-model", "messages": [{"role": "user", "content": "hi"}],
                      "stream": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 429, f"上游 429 应映射为 429，实际 {resp.status_code}: {resp.text[:200]}"
            assert resp.json()["error"]["type"] == "rate_limit_error"
    print("  [F] 上游限流(429) → 网关 429 rate_limit_error（客户端可正确退避）  ✓")


def test_gateway_upstream_auth_mapped():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        gw_mod.LLMClient = lambda c, m: FailingLLM(c, m, kind="auth")
        app = create_app(cfg)
        token = _mint_token(cfg.security.secret)
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "demo/demo-model", "messages": [{"role": "user", "content": "hi"}],
                      "stream": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401, f"缺 Key 应映射为 401，实际 {resp.status_code}: {resp.text[:200]}"
            assert resp.json()["error"]["type"] == "authentication_error"
    print("  [G] 上游缺 Key/鉴权 → 网关 401 authentication_error  ✓")


if __name__ == "__main__":
    main()
