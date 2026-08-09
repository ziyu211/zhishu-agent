"""审计缺口修复回归测试：令牌吊销(G2) / 向量记忆可观测(G1) / 脱敏统计(G6)。

直接对 core 层做单元验证，无需启动 HTTP 服务，聚焦逻辑正确性。
"""
import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zhishu.core.security import AuthService, SecurityConfig, UserStore, Crypto
from zhishu.core.redact import Redactor


def _cfg():
    return SecurityConfig(secret="test-secret", enable_auth=True)


def test_token_revocation_roundtrip():
    tmp = tempfile.mkdtemp()
    revoked = os.path.join(tmp, "revoked.json")
    cfg = _cfg()
    crypto = Crypto(False)
    users = UserStore(crypto, path=os.path.join(tmp, "u.db"))
    users.bootstrap("admin", "admin123")
    auth = AuthService(cfg, users=users, revoked_path=revoked)

    # 签发令牌
    sess = auth.login("admin", "admin123")
    assert sess and sess["token"]
    # 验证通过
    data = auth.verify(sess["token"])
    assert data and data.get("u") == "admin"
    assert "jti" in data  # 新令牌带 jti

    # 吊销后失效
    auth.revoke_token(data["jti"])
    assert auth.verify(sess["token"]) is None
    # 持久化：新实例加载后仍失效
    auth2 = AuthService(cfg, users=users, revoked_path=revoked)
    assert auth2.verify(sess["token"]) is None
    # 未吊销的旧令牌（无 jti）不受影响
    old = auth._token("admin", "admin")
    assert auth.verify(old)


def test_vector_memory_stats_and_clear():
    from zhishu.core.memory.backends import BuiltinMemoryBackend
    from zhishu.core.config import ZhishuConfig

    tmp = tempfile.mkdtemp()
    cfg = ZhishuConfig()
    cfg.embedding.enabled = False  # 隔离真实 embedding，避免外部依赖
    backend = BuiltinMemoryBackend(cfg, tmp)
    # stats 在 embedding 关闭时 add 可能失败，但 stats 不应抛错
    s = backend.stats(owner="alice")
    assert s["backend"] == "builtin"
    assert "count" in s
    # clear 不抛错且返回 int
    assert isinstance(backend.clear(owner="alice"), int)


def test_redact_stats_counts():
    r = Redactor(enabled=True)
    r.stats = {"calls": 0, "masked": 0}
    out = r.redact("我的手机是 13800138000，邮箱 a@b.com")
    assert "13800138000" not in out
    assert r.stats["calls"] == 1
    assert r.stats["masked"] >= 2  # 手机 + 邮箱
    # 敏感键整体遮蔽也计数
    r.redact_dict({"password": "secret123"})
    assert r.stats["masked"] >= 3


if __name__ == "__main__":
    test_token_revocation_roundtrip()
    test_vector_memory_stats_and_clear()
    test_redact_stats_counts()
    print("ALL_GAP_FIX_TESTS_PASSED")
