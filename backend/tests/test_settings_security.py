"""设置页「安全与网络」开关组 —— 回归测试。

覆盖：
  A. GET /api/v1/settings 同时返回 memory 与 security 两组；security 含 7 个运行时开关。
  B. POST /api/v1/settings 切换 security 字段后：响应即时反映新值，且进程内
     ctx.cfg.security / ctx.audit.enable / ctx.redactor.enabled 同步生效（免重启）。
  C. 覆盖持久化到 data_dir/config.override.json；模拟重启（新建 AppContext 复用同 data_dir）
     后 _apply_override 能正确回放 security 组（仅覆盖已保存字段，未存字段保持默认）。
  D. 非 admin（viewer）访问设置端点 → 403。

运行：python tests/test_settings_security.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.config import ZhishuConfig, ProviderConfig  # noqa: E402
from zhishu.context import AppContext, get_ctx  # noqa: E402
from zhishu.main import create_app  # noqa: E402
from zhishu.core.security import Crypto  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SECURITY_KEYS = (
    "allow_private_fetch", "outbound_allow", "allow_code_exec", "allow_shell",
    "shell_enforce_allowlist", "enable_audit", "enable_redact",
)


def _tmpdir():
    return tempfile.mkdtemp(prefix="zhishu_set_")


def _build_cfg(tmp: str) -> ZhishuConfig:
    cfg = ZhishuConfig()
    cfg.server.data_dir = tmp
    cfg.security.enable_sm = False
    cfg.security.enable_auth = True
    cfg.security.secret = "change-me-zhishu-secret"
    cfg.security.enable_audit = True
    cfg.security.enable_redact = True
    cfg.security.allow_private_fetch = False
    cfg.providers = {
        "demo": ProviderConfig(name="demo", label="Demo", base_url="http://demo",
                               models=["demo-model"], enabled=True, owner="", shared=False),
    }
    return cfg


def _mint_token(secret: str, user: str = "admin", role: str = "admin", ttl: int = 86400 * 7) -> str:
    payload = json.dumps({"u": user, "r": role, "exp": int(time.time()) + ttl})
    sig = Crypto(False).sign(secret, payload)
    return f"{payload}.{sig}"


def test_settings_get_returns_security_group():
    tmp = _tmpdir()
    try:
        cfg = _build_cfg(tmp)
        app = create_app(cfg)
        with TestClient(app) as client:
            token = _mint_token(cfg.security.secret)
            r = client.get("/api/v1/settings", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert "memory" in body and "security" in body
            for k in SECURITY_KEYS:
                assert k in body["security"], f"security 缺少字段 {k}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_settings_post_security_toggle_and_persist():
    tmp = _tmpdir()
    try:
        cfg = _build_cfg(tmp)
        app = create_app(cfg)
        with TestClient(app) as client:
            token = _mint_token(cfg.security.secret)
            h = {"Authorization": f"Bearer {token}"}
            patch = {
                "security": {
                    "allow_private_fetch": True,
                    "enable_audit": False,
                    "enable_redact": False,
                }
            }
            r = client.post("/api/v1/settings", json=patch, headers=h)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["security"]["allow_private_fetch"] is True
            assert body["security"]["enable_audit"] is False
            assert body["security"]["enable_redact"] is False
            # 未提及字段保持默认（不被清零）
            assert body["security"]["outbound_allow"] is False
            # 进程内即时生效
            ctx = get_ctx()
            assert ctx.cfg.security.allow_private_fetch is True
            assert ctx.audit.enable is False
            assert ctx.redactor.enabled is False
            # 持久化到 config.override.json
            ov_path = os.path.join(tmp, "config.override.json")
            assert os.path.exists(ov_path)
            with open(ov_path, "r", encoding="utf-8") as f:
                ov = json.load(f)
            assert ov["security"]["allow_private_fetch"] is True
            assert ov["security"]["enable_audit"] is False
            assert "memory" not in ov  # 仅 security 被本请求涉及，不污染 memory 组
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_override_round_trip_on_restart():
    """模拟重启：新建 AppContext 复用同 data_dir，_apply_override 应回放 security 组。"""
    tmp = _tmpdir()
    try:
        cfg = _build_cfg(tmp)
        app = create_app(cfg)
        with TestClient(app) as client:
            token = _mint_token(cfg.security.secret)
            client.post(
                "/api/v1/settings",
                json={"security": {"allow_private_fetch": True, "outbound_allow": True}},
                headers={"Authorization": f"Bearer {token}"},
            )
        # 模拟重启：复用同 data_dir 重新构建 AppContext
        cfg2 = _build_cfg(tmp)
        ctx2 = AppContext(cfg2)
        assert ctx2.cfg.security.allow_private_fetch is True
        assert ctx2.cfg.security.outbound_allow is True
        # 未保存字段保持默认
        assert ctx2.cfg.security.enable_audit is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_settings_forbidden_for_viewer():
    tmp = _tmpdir()
    try:
        cfg = _build_cfg(tmp)
        app = create_app(cfg)
        with TestClient(app) as client:
            # 创建一个真实 viewer 用户（否则令牌会因「未知用户」被拒为 401，而非权限不足 403）
            ctx = get_ctx()
            ctx.users.create("viewer", "viewerpw", role="viewer")
            token = _mint_token(cfg.security.secret, user="viewer", role="viewer")
            h = {"Authorization": f"Bearer {token}"}
            assert client.get("/api/v1/settings", headers=h).status_code == 403
            assert client.post("/api/v1/settings", json={"security": {"allow_shell": False}},
                               headers=h).status_code == 403
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_settings_get_returns_security_group()
    test_settings_post_security_toggle_and_persist()
    test_override_round_trip_on_restart()
    test_settings_forbidden_for_viewer()
    print("ALL PASS")
