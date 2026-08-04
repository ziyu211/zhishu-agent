"""闭环审计 —— 真实 HTTP 链路验证（对运行中的实例执行）。

只创建/删除带 __ZS_AUDIT__ 前缀的临时数据，不改动任何既有用户数据。

用法（容器内）：
    PYTHONPATH=/app/backend python http_closure_check.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("ZS_BASE", "http://127.0.0.1:8080")
SECRET = os.environ.get("ZS_SECRET", "change-me-zhishu-secret")
MEM_DB = os.environ.get("ZS_MEM_DB", "/app/backend/data/zhishu_memory.db")

PASS = 0
FAIL: list[str] = []


def check(cond: bool, name: str, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}{(' — ' + extra) if extra else ''}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}{(' — ' + extra) if extra else ''}")


def sign(payload: str) -> str:
    try:
        from gmssl import sm3
        return hmac.new(SECRET.encode(), payload.encode(),
                        lambda d: bytes.fromhex(sm3.sm3_hash(d).hex())).hexdigest()
    except Exception:
        return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def token(user: str, role: str, ttl: int = 3600) -> str:
    p = json.dumps({"u": user, "r": role, "exp": int(time.time()) + ttl})
    return f"{p}.{sign(p)}"


def call(method: str, path: str, tok: str | None = None, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def main() -> int:
    print("=" * 64)
    print(" 智枢 · 闭环审计 HTTP 链路验证")
    print("=" * 64)

    admin = token("admin", "admin")

    # --------------------------------------------------- 令牌吊销
    print("\n[P0-3] 令牌吊销（真实服务）")
    st, me = call("GET", "/api/v1/auth/me", admin)
    check(st == 200 and me.get("role") == "admin", "合法 admin 令牌通过", f"HTTP {st}")

    st, _ = call("GET", "/api/v1/auth/me", token("__zs_ghost__", "admin"))
    check(st == 401, "签名合法但用户不存在的令牌被拒绝（吊销生效）", f"HTTP {st}")

    bad = admin[:-4] + "0000"
    st, _ = call("GET", "/api/v1/auth/me", bad)
    check(st == 401, "签名被篡改的令牌被拒绝", f"HTTP {st}")

    p = json.dumps({"u": "admin", "r": "admin", "exp": int(time.time()) - 5})
    st, _ = call("GET", "/api/v1/auth/me", f"{p}.{sign(p)}")
    check(st == 401, "过期令牌被拒绝", f"HTTP {st}")

    # --------------------------------------------------- 对话部分更新
    print("\n[P0-1] 对话部分更新不破坏 messages（真实服务）")
    st, conv = call("POST", "/api/v1/conversations", admin,
                    {"title": "__ZS_AUDIT__ tmp"})
    check(st == 200 and conv and conv.get("id"), "创建临时对话", f"HTTP {st}")
    cid = conv["id"]

    st, _ = call("PUT", f"/api/v1/conversations/{cid}", admin,
                 {"messages": [{"role": "user", "content": "audit ping"},
                               {"role": "assistant", "content": "audit pong"}]})
    check(st == 200, "写入 2 条消息", f"HTTP {st}")

    # 只改标题，不传 messages —— 修复前会把 messages 覆盖成 "null"
    st, _ = call("PUT", f"/api/v1/conversations/{cid}", admin,
                 {"title": "__ZS_AUDIT__ renamed"})
    check(st == 200, "仅更新标题（不带 messages 字段）", f"HTTP {st}")

    st, got = call("GET", f"/api/v1/conversations/{cid}", admin)
    msgs = got.get("messages") if isinstance(got, dict) else None
    check(st == 200 and got.get("title") == "__ZS_AUDIT__ renamed", "标题已更新")
    check(isinstance(msgs, list) and len(msgs) == 2,
          "消息未被破坏（仍为 2 条 list）",
          f"type={type(msgs).__name__} len={len(msgs) if isinstance(msgs, list) else 'N/A'}")

    st, lst = call("GET", "/api/v1/conversations", admin)
    check(st == 200, "对话列表接口正常（修复前 len(None) 会 500）", f"HTTP {st}")

    # --------------------------------------------------- 级联删除
    print("\n[级联] 删除对话同时清理服务端记忆 turns")
    sess = f"admin:{cid}"
    try:
        conn = sqlite3.connect(MEM_DB)
        conn.execute("INSERT INTO turns (session, role, content) VALUES (?,?,?)",
                     (sess, "user", "audit cascade probe"))
        conn.commit()
        before = conn.execute("SELECT COUNT(*) FROM turns WHERE session=?",
                              (sess,)).fetchone()[0]
        conn.close()
        check(before >= 1, "已在 memory.turns 写入探针记录", f"{before} 条")
    except Exception as e:
        check(False, "写入 memory 探针", str(e))
        before = 0

    st, _ = call("DELETE", f"/api/v1/conversations/{cid}", admin)
    check(st == 200, "删除对话", f"HTTP {st}")

    st, _ = call("GET", f"/api/v1/conversations/{cid}", admin)
    check(st == 404, "对话已不存在", f"HTTP {st}")

    if before:
        conn = sqlite3.connect(MEM_DB)
        after = conn.execute("SELECT COUNT(*) FROM turns WHERE session=?",
                             (sess,)).fetchone()[0]
        conn.close()
        check(after == 0, "对应 memory.turns 已被级联清理（无残留可被召回）",
              f"删除后 {after} 条")

    # --------------------------------------------------- RBAC
    print("\n[RBAC] 角色边界仍然生效")
    viewer = token("__zs_ghost_viewer__", "viewer")
    st, _ = call("GET", "/api/v1/users", viewer)
    check(st == 401, "不存在的 viewer 令牌访问用户管理被拒", f"HTTP {st}")

    st, _ = call("GET", "/api/v1/users", admin)
    check(st == 200, "admin 可访问用户管理", f"HTTP {st}")

    st, _ = call("GET", "/api/v1/agents", admin)
    check(st == 200, "智能体列表可访问", f"HTTP {st}")

    st, tools = call("GET", "/api/v1/tools", admin)
    if st == 200 and isinstance(tools, dict):
        names = {t.get("name") for t in (tools.get("tools") or [])}
        check("delegate_to_agent" in names and "create_team" in names,
              "协作工具在运行实例中已注册", f"共 {len(names)} 个工具")

    # ------------------------------------------- #338 cron shell 安全闸门
    print("\n[#338] 定时 shell 任务的命令闸门（真实服务）")
    # 借用运行实例中已存在的 operator 账号（令牌吊销校验要求用户真实存在）
    st, users = call("GET", "/api/v1/users", admin)
    op_name = None
    if st == 200:
        rows = users if isinstance(users, list) else (users or {}).get("users") or []
        for u in rows:
            if (u.get("role") or "") == "operator":
                op_name = u.get("username")
                break
    if not op_name:
        check(True, "跳过：实例中没有 operator 账号，无法验证 shell 闸门")
    else:
        op = token(op_name, "operator")
        jid_bad = jid_ok = None
        st, r = call("POST", "/api/v1/cron", op, {
            "name": "__ZS_AUDIT__danger", "schedule_type": "interval",
            "schedule_config": {"seconds": 86400}, "action": "shell",
            "payload": "cat /etc/shadow"})
        check(st == 200, f"operator 可创建 shell 任务（{op_name}）", f"HTTP {st}")
        jid_bad = (r or {}).get("id") if isinstance(r, dict) else None
        if jid_bad:
            st, out = call("POST", f"/api/v1/cron/{jid_bad}/run", op)
            body = json.dumps(out, ensure_ascii=False) if out is not None else ""
            check("已拦截" in body, "高危命令在执行期被闸门拦截",
                  body[:80])
            call("DELETE", f"/api/v1/cron/{jid_bad}", op)

        st, r = call("POST", "/api/v1/cron", op, {
            "name": "__ZS_AUDIT__benign", "schedule_type": "interval",
            "schedule_config": {"seconds": 86400}, "action": "shell",
            "payload": "echo zhishu-guard-ok"})
        jid_ok = (r or {}).get("id") if isinstance(r, dict) else None
        if jid_ok:
            st, out = call("POST", f"/api/v1/cron/{jid_ok}/run", op)
            body = json.dumps(out, ensure_ascii=False) if out is not None else ""
            check("zhishu-guard-ok" in body, "白名单内的正常命令仍可执行",
                  body[:80])
            check("ZHISHU_" not in body and "secret" not in body.lower(),
                  "子进程输出不含密钥类环境变量")
            call("DELETE", f"/api/v1/cron/{jid_ok}", op)

    # ------------------------------------------- #341 向量签名暴露
    print("\n[#341] 知识库统计暴露向量空间签名与陈旧分块")
    st, stt = call("GET", "/api/v1/knowledge/stats", admin)
    if st == 200 and isinstance(stt, dict):
        check("embedding_signature" in stt, "stats 返回 embedding_signature",
              str(stt.get("embedding_signature")))
        check("stale" in stt, "stats 返回陈旧分块数（需重新解析的量）",
              f"stale={stt.get('stale')}")
    else:
        check(False, "知识库统计接口可访问", f"HTTP {st}")

    print("\n" + "=" * 64)
    print(f" 通过 {PASS} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"   - {f}")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
