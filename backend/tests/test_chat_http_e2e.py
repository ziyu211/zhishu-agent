"""智枢 chat API —— 真实 HTTP 全链路冒烟测试（不依赖外部 LLM / 网络）。

与 test_multiagent_e2e.py（进程内直调 Agent）互补：本测试用 FastAPI TestClient
真正走 HTTP 中间件 → 鉴权(require_auth) → 并发限流器(ConcurrencyLimiter) →
chat 路由(event_gen) → SSE 序列化 全链路。LLM 用 FakeLLM 打桩，故不依赖外部服务。

覆盖：
  A. 鉴权闸门：无 token 请求 /api/v1/chat → 401
  B. 自签 admin token 绕过登录，全链路对话：status/token/done 事件齐全、回复正确、
     限流器正常接入（done 出现 = acquire 后已 release，无挂死 / 无信号量泄漏）
  C. 限流拒绝路径在 HTTP 层生效：daily_quota_per_user=1 时，同日第二次对话被友好拒绝
     （error 事件含「额度」）

运行方式：
  * 独立运行：  python tests/test_chat_http_e2e.py
  * pytest：    pytest tests/test_chat_http_e2e.py -o asyncio_mode=auto
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time

# 让脚本无论从哪个目录启动都能 import 到 zhishu 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 本地开发默认密钥放行（代码侧硬闸门会在 enable_auth + 默认密钥时拒绝启动）
os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.config import ZhishuConfig
from zhishu.context import get_ctx
from zhishu.main import create_app
from zhishu.core.agent import agent as agent_mod
from zhishu.core.security import Crypto
from fastapi.testclient import TestClient



# ---------------------------------------------------------------------------
# 临时数据目录：sqlite 连接在测试结束时未必已关闭，Windows 上会让目录删除抛
# WinError 32（另一个程序正在使用此文件），把「清理失败」误报成「测试失败」，
# 掩盖真实断言结果。清理是尽力而为，失败直接忽略。
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _tmpdir():
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------------------
# FakeLLM：取代真实模型，返回确定性文本，一轮即终止
# （run 主循环遇纯文本回复即 yield token → done → return，不会死循环）
# ---------------------------------------------------------------------------
REPLY_MARK = "【HTTP冒烟回复-智枢】"


class FakeLLM:
    def __init__(self, cfg, api_mode=None):
        self.cfg = cfg
        self.api_mode = api_mode

    # 签名须与 LLMClient.chat 保持一致（含 tool_choice），否则真实调用点传参会
    # 抛 TypeError 并被 run() 兜底成 error 事件，测试表象是「回复缺失」而非签名不符。
    # 用 **_kw 兜住后续新增的可选参数，避免生产接口每加一个参数就要改桩。
    async def chat(self, messages, model=None, tools=None, max_tokens=None,
                   tool_choice=None, **_kw):
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": f"{REPLY_MARK}你好，我是智枢，这是一次 HTTP 全链路冒烟测试。",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }]
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _build_cfg(tmp: str) -> ZhishuConfig:
    cfg = ZhishuConfig()
    cfg.server.data_dir = tmp
    cfg.security.enable_sm = False          # 用 SHA256，避免依赖 gmssl
    cfg.security.enable_auth = True
    cfg.security.secret = "change-me-zhishu-secret"   # 与自签 token 同源
    cfg.security.enable_audit = True
    cfg.agent.delegate_timeout = 60.0
    # 打桩 LLMClient（双保险：主管 ctx.llm 实例 + run 内部可能重建的路径）
    agent_mod.LLMClient = FakeLLM
    return cfg


def _mint_token(secret: str, user: str = "admin", role: str = "admin",
               ttl: int = 86400 * 7) -> str:
    """用 app 同源密钥自签 admin token —— 等价于「绕过登录」拿到合法凭证。

    与 AuthService._token 使用完全相同的 Crypto.sign，故 verify 必然通过。
    """
    payload = json.dumps({"u": user, "r": role, "exp": int(time.time()) + ttl})
    sig = Crypto(False).sign(secret, payload)
    return f"{payload}.{sig}"


def _parse_sse(text: str) -> list:
    """从 SSE 文本中抽取所有 data: 行的 JSON 事件（忽略 : ping 注释行）。"""
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


# ---------------------------------------------------------------------------
# 测试 A：鉴权闸门
# ---------------------------------------------------------------------------
def test_chat_auth_required():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        app = create_app(cfg)
        with TestClient(app) as client:
            resp = client.post("/api/v1/chat", json={"message": "你好"})
            assert resp.status_code == 401, \
                f"期望 401，实际 {resp.status_code}: {resp.text[:200]}"
    print("  [A] 未带 token 请求 /api/v1/chat → 401（鉴权闸门生效）  ✓")


# ---------------------------------------------------------------------------
# 测试 B：自签 token 全链路对话
# ---------------------------------------------------------------------------
def test_chat_full_chain_signed():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        app = create_app(cfg)
        ctx = get_ctx()
        ctx.llm = FakeLLM(cfg)          # 覆盖主管 LLM（双保险）
        ctx.memory_manager = None       # 关闭长期记忆同步，避免额外 LLM 调用
        token = _mint_token(cfg.security.secret)
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/chat",
                json={"message": "你好，介绍一下自己", "session": "http-e2e-b"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, \
                f"期望 200，实际 {resp.status_code}: {resp.text[:300]}"
            events = _parse_sse(resp.text)
            types = [e.get("type") for e in events]
            assert any(t in ("status", "done") for t in types), \
                f"缺少状态事件: {types}"
            assert REPLY_MARK in resp.text, \
                f"回复未出现 FakeLLM 标记:\n{resp.text[:400]}"
            assert not any(e.get("type") == "error" for e in events), \
                f"出现未预期错误: {events}"
            # done 事件存在 ⇒ 限流 acquire 后已 release（无挂死 / 无信号量泄漏）
            assert "done" in types, f"缺少 done 事件: {types}"
    print("  [B] 自签 token 全链路对话（鉴权→限流接入→SSE 流式）  ✓")


# ---------------------------------------------------------------------------
# 测试 C：限流拒绝路径在 HTTP 层生效（每日配额耗尽）
# ---------------------------------------------------------------------------
def test_chat_daily_quota_reject():
    with _tmpdir() as tmp:
        cfg = _build_cfg(tmp)
        cfg.agent.daily_quota_per_user = 1   # 同一用户对同一自然日仅 1 次对话
        app = create_app(cfg)
        ctx = get_ctx()
        ctx.llm = FakeLLM(cfg)
        ctx.memory_manager = None
        token = _mint_token(cfg.security.secret)
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {token}"}
            r1 = client.post("/api/v1/chat",
                             json={"message": "第一次对话", "session": "http-e2e-c1"},
                             headers=headers)
            assert REPLY_MARK in r1.text, f"首次对话应成功:\n{r1.text[:300]}"
            r2 = client.post("/api/v1/chat",
                             json={"message": "第二次对话", "session": "http-e2e-c2"},
                             headers=headers)
            ev2 = _parse_sse(r2.text)
            errs = [e for e in ev2 if e.get("type") == "error"]
            assert errs, f"第二次（配额耗尽）应被拒，但无 error 事件: {ev2}"
            assert "额度" in (errs[0].get("message") or ""), \
                f"拒绝文案应含『额度』: {errs[0]}"
    print("  [C] 每日配额耗尽 → HTTP 层友好拒绝（error:额度）  ✓")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------
def main():
    try:
        test_chat_auth_required()
        test_chat_full_chain_signed()
        test_chat_daily_quota_reject()
    except AssertionError as e:
        print("FAILED:", e)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print("ERROR:", repr(e))
        sys.exit(2)
    print("\nALL CHAT HTTP E2E TESTS PASSED")


if __name__ == "__main__":
    main()
