"""闭环审计修复回归测试。

覆盖本轮「全量闭环审计」修复的关键断点，全部使用临时目录，不触碰生产数据：

  P0-1  对话部分更新破坏 messages（写成 "null" -> len(None) -> 列表接口 500）
  P0-2  轮换 security.secret 静默清空 Provider 密钥
  P0-3  令牌吊销（删除/停用/降级），且不得误杀「无用户库/引导期」的合法令牌
  P1-6  cron 中 sqlite3.Row 无 .get()
  HIGH-4 内置工具自发现静默吞异常导致工具全量消失
  HIGH-5 并发信号量在 acquire 被取消时永久泄漏 -> 全实例死锁
  级联   删除对话/用户时清理 memory turns、Provider、对话

运行： PYTHONPATH=backend python backend/tests/test_closure_audit.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

PASS = 0
FAIL: list[str] = []


def check(cond: bool, name: str) -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}")


# ---------------------------------------------------------------- P0-1
def test_conversation_partial_update():
    print("\n[P0-1] 对话部分更新不得破坏 messages")
    from zhishu.core.conversations import ConversationStore

    with tempfile.TemporaryDirectory() as d:
        st = ConversationStore(os.path.join(d, "c.db"))
        c = st.create(owner="u1", title="原标题")
        cid = c["id"]
        st.update(cid, "u1", "user", messages=[{"role": "user", "content": "hi"}])

        # 只更新 title，不传 messages —— 修复前会把 messages 覆盖成 "null"
        st.update(cid, "u1", "user", title="新标题")
        row = st.get_for(cid, "u1", "user")
        check(row is not None, "部分更新后仍可读取")
        check(row["title"] == "新标题", "title 已更新")
        check(isinstance(row["messages"], list), "messages 仍为 list（未被写成 null）")
        check(len(row["messages"]) == 1, "messages 内容未丢失")

        # 列表接口不得抛异常（修复前 len(None) -> TypeError -> HTTP 500）
        items = st.list(owner="u1")
        check(len(items) == 1, "列表接口正常返回（无 len(None) 500）")

        # 显式传 messages 仍应生效
        st.update(cid, "u1", "user", messages=[{"role": "user", "content": "a"},
                                               {"role": "assistant", "content": "b"}])
        check(len(st.get_for(cid, "u1", "user")["messages"]) == 2, "显式更新 messages 生效")

        # 级联删除：按 owner 清空
        st.create(owner="u1", title="t2")
        st.create(owner="u2", title="t3")
        n = st.delete_by_owner("u1")
        check(n == 2, f"delete_by_owner 删除本人全部对话（实删 {n}）")
        check(len(st.list(owner="u2")) == 1, "delete_by_owner 不误删他人对话")


# ---------------------------------------------------------------- P0-3
def test_token_revocation():
    print("\n[P0-3] 令牌吊销 + 不误杀引导期/匿名令牌")
    from zhishu.core.config import SecurityConfig
    from zhishu.core.security import AuthService, Crypto, UserStore

    with tempfile.TemporaryDirectory() as d:
        cfg = SecurityConfig(enable_auth=True, secret="test-secret-123",
                             admin_user="admin", admin_password="pw123456")
        crypto = Crypto(cfg.enable_sm)
        users = UserStore(crypto, os.path.join(d, "u.db"))

        # a) 无用户库（关闭鉴权 / 单机匿名）不得误杀
        auth_nodb = AuthService(cfg, users=None)
        check(auth_nodb.verify(auth_nodb._token("anonymous", "admin")) is not None,
              "无用户库时令牌放行（不锁死系统）")

        # b) 用户库为空（bootstrap 之前）不得误杀
        auth = AuthService(cfg, users=users)
        check(auth.verify(auth._token("admin", "admin")) is not None,
              "用户库为空时令牌放行（引导期）")

        # 建两个用户（保证删 alice 时库仍非空，且始终留一个 admin）
        users.create("root", "pw123456", role="admin")
        users.create("alice", "pw123456", role="operator")
        tok_alice = auth._token("alice", "operator")
        check(auth.verify(tok_alice) is not None, "正常用户令牌有效")

        row = users.get_by_name("alice")
        uid = row["id"]

        # c) 角色降级 -> 令牌里的旧角色不再被信任
        users.update(uid, role="viewer")
        d2 = auth.verify(tok_alice)
        check(d2 is not None and d2.get("r") == "viewer",
              "角色降级后令牌角色被强制刷新为 viewer（降级立即生效）")

        # d) 停用 -> 立即失效
        users.update(uid, status="disabled")
        check(auth.verify(tok_alice) is None, "用户停用后旧令牌立即失效")

        # e) 恢复 -> 再次有效
        users.update(uid, status="active")
        check(auth.verify(tok_alice) is not None, "恢复启用后令牌重新有效")

        # f) 删除 -> 立即失效（库非空）
        users.delete(uid)
        check(auth.verify(tok_alice) is None, "用户删除后旧令牌立即失效（吊销生效）")

        # g) 签名篡改
        tok_root = auth._token("root", "admin")
        check(auth.verify(tok_root[:-4] + "0000") is None, "签名被篡改的令牌被拒绝")

        # h) 过期
        import json as _json
        import time as _time
        payload = _json.dumps({"u": "root", "r": "admin", "exp": int(_time.time()) - 10})
        expired = f"{payload}.{crypto.sign(cfg.secret, payload)}"
        check(auth.verify(expired) is None, "过期令牌被拒绝")


# ---------------------------------------------------------------- P0-2
def test_credential_key_preserved():
    print("\n[P0-2] 轮换 secret 不得静默清空已存 Provider 密钥")
    from zhishu.core.config import ZhishuConfig
    from zhishu.core.credentials import ProviderStore

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "providers.json")

        cfg1 = ZhishuConfig()
        cfg1.security.secret = "secret-A"
        st1 = ProviderStore(cfg1, p)
        st1.add(name="t_dash", label="测试", base_url="https://x/v1",
                api_key="sk-real-key-0001", models=["m1"])
        check(cfg1.providers["t_dash"].api_key == "sk-real-key-0001", "写入后内存持有明文")
        blob = open(p, "r", encoding="utf-8").read()
        check("sk-real-key-0001" not in blob, "落盘为密文（明文不落盘）")

        # 换 secret 后加载：解不开 -> 明文为空，但必须保留原密文
        cfg2 = ZhishuConfig()
        cfg2.security.secret = "secret-B"
        st2 = ProviderStore(cfg2, p)
        st2._save()  # 修复前这一步会把密文写成空串，密钥永久丢失
        blob2 = open(p, "r", encoding="utf-8").read()
        has_cipher = ('"api_key": "xor:' in blob2) or ('"api_key": "sm4:' in blob2)
        check(has_cipher, "换 secret 并保存后，原密文仍在文件中（未被清空）")

        # 用回原 secret 仍能解出 -> 可救回
        cfg3 = ZhishuConfig()
        cfg3.security.secret = "secret-A"
        ProviderStore(cfg3, p)
        check(cfg3.providers["t_dash"].api_key == "sk-real-key-0001",
              "换回原 secret 可完整恢复密钥（可救回，非不可逆丢失）")

        # 级联删除
        cfg3.providers["t_dash"].owner = "u1"
        st3 = ProviderStore(cfg3, p)
        st3.cfg.providers["t_dash"].owner = "u1"
        n = st3.delete_by_owner("u1")
        check(n >= 1, f"delete_by_owner 清理该用户 Provider（实删 {n}）")


# ---------------------------------------------------------------- HIGH-5
def test_concurrency_no_leak():
    print("\n[HIGH-5] 并发信号量：acquire 被取消不得泄漏许可")
    from zhishu.core.concurrency import ConcurrencyLimiter

    async def run() -> bool:
        lim = ConcurrencyLimiter()
        # 全局 2、单用户 1：制造「全局拿到、单用户阻塞」的窗口
        lim.configure(global_limit=2, per_user_limit=1)

        await lim.acquire("u1")           # u1 占满自己的额度（全局用 1）

        async def blocked():
            await lim.acquire("u1")       # 卡在 per-user 信号量上（已先拿走 1 个全局）

        t = asyncio.create_task(blocked())
        await asyncio.sleep(0.05)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

        await lim.release("u1")           # 释放最初那次占用

        # 修复前：被取消的那次已吞掉 1 个全局许可且永不归还 -> 这里会挂死
        await lim.acquire("a")
        await lim.acquire("b")
        await lim.release("a")
        await lim.release("b")
        return True

    try:
        ok = asyncio.run(asyncio.wait_for(run(), timeout=5))
    except asyncio.TimeoutError:
        ok = False
    check(ok, "取消 acquire 后全局许可已回滚（无泄漏、无死锁）")


# ---------------------------------------------------------------- HIGH-4
def test_tool_discovery_fail_loud():
    print("\n[HIGH-4] 内置工具自发现：失败不得静默置位")
    from zhishu.core.tools.registry import ToolRegistry

    ToolRegistry.discover_builtin_tools()
    names = {t.name for t in ToolRegistry.all()}
    check(len(names) > 5, f"内置工具已注册（共 {len(names)} 个）")
    for must in ("delegate_to_agent", "create_team"):
        check(must in names, f"关键协作工具存在：{must}")

    # 失败时不得置位 _discovered（保证下次仍会重试，而非工具全量永久消失）
    import inspect
    src = inspect.getsource(ToolRegistry.discover_builtin_tools)
    ok_order = src.index("cls._discovered = True") > src.index("except Exception")
    check(ok_order, "置位语句在异常处理之后（失败则不置位，可重试）")
    check("exception(" in src or "error(" in src, "自发现失败会输出错误日志（fail-loud）")


# ---------------------------------------------------------------- P1-6
def test_cron_row_get():
    print("\n[P1-6] sqlite3.Row 无 .get()：cron 角色回查不得抛异常")
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE users (username TEXT, role TEXT)")
    c.execute("INSERT INTO users VALUES ('admin','admin')")
    row = c.execute("SELECT * FROM users").fetchone()
    raised = False
    try:
        row.get("role")            # 修复前的写法
    except AttributeError:
        raised = True
    check(raised, "确认 sqlite3.Row 确实没有 .get()（原写法必崩）")
    check(dict(row).get("role") == "admin", "dict(row).get() 写法正确返回角色")

    # 源码层面确认 cron.py 已改正（用模块自省定位，避免依赖脚本所在目录）
    import inspect
    from zhishu.core import cron as _cron
    src = inspect.getsource(_cron)
    check("row.get(" not in src, "cron.py 中已无 row.get( 的错误写法")
    check("dict(row).get(" in src, "cron.py 使用 dict(row).get() 安全取值")


# ---------------------------------------------------------------- 级联
def test_memory_prefix_escape():
    print("\n[级联] 记忆按 owner:session 前缀清理，LIKE 通配符须转义")
    from zhishu.core.memory.sqlite_provider import MemoryStore

    with tempfile.TemporaryDirectory() as d:
        m = MemoryStore(os.path.join(d, "m.db"))
        for sid in ("u1:s1", "u1:s2", "u10:s1", "u_x:s1"):
            m.append(sid, "user", "hello")

        n = m.clear_session_prefix("u1:")
        check(n == 2, f"仅清理 u1: 前缀（实删 {n}）")
        check(len(m.history("u10:s1")) == 1, "不误删 u10:s1（前缀相近）")

        # 下划线是 LIKE 通配符，未转义会把 u1x/uAx 之类一起删掉
        n2 = m.clear_session_prefix("u_x:")
        check(n2 == 1, f"下划线被正确转义，仅删 u_x（实删 {n2}）")
        check(len(m.history("u1:s1")) == 0, "已删会话读取为空")


def main() -> int:
    print("=" * 64)
    print(" 智枢 · 闭环审计修复回归测试")
    print("=" * 64)
    for fn in (test_conversation_partial_update, test_token_revocation,
               test_credential_key_preserved, test_concurrency_no_leak,
               test_tool_discovery_fail_loud, test_cron_row_get,
               test_memory_prefix_escape):
        try:
            fn()
        except Exception as e:
            import traceback
            FAIL.append(f"{fn.__name__} 异常: {e}")
            print(f"  [ERROR] {fn.__name__}: {e}")
            traceback.print_exc()
    print("\n" + "=" * 64)
    print(f" 通过 {PASS} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"   - {f}")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
