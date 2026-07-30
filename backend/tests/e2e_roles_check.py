# -*- coding: utf-8 -*-
"""四角色端到端验证：本轮安全闭环修复（cron shell 限权 / 公共 Provider 保护 / SSRF 防护 / RBAC 门控）。
运行：python e2e_roles_check.py  （需容器 zsagent 运行于 localhost:8080）
"""
import json
import os
import urllib.error
import urllib.request

# 目标环境可用环境变量覆盖，便于对远程部署实例做同一套验证：
#   ZHISHU_BASE=http://1.2.3.4:8090 ZHISHU_ADMIN_P='xxx' python e2e_roles_check.py
BASE = os.getenv("ZHISHU_BASE", "http://localhost:8080").rstrip("/") + "/api/v1"
ADMIN_U = os.getenv("ZHISHU_ADMIN_U", "admin")
ADMIN_P = os.getenv("ZHISHU_ADMIN_P", "zhishu@2026")
# 四角色测试账号：若容器中不存在则由 admin 自动创建（密码固定），保证本脚本可重复运行。
FIXTURES = {
    "admin": (ADMIN_U, ADMIN_P),
    "operator": ("rs_op", "Operator@2026"),
    "user": ("rs_user", "User@2026"),
    "viewer": ("rs_viewer", "Viewer@2026"),
}
ROLE_OF = {"admin": "admin", "operator": "operator", "user": "user", "viewer": "viewer"}
_created_users: set = set()

PASS, FAIL = [], []


def req(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + str(detail)[:120] if detail else ""))


# admin 登录（用于按需创建缺失的测试账号）
st, _ad = req("POST", "/auth/login", body={"username": ADMIN_U, "password": ADMIN_P})
admin_token = _ad.get("token", "")
check("login:admin", st == 200 and admin_token, f"perms={_ad.get('perms')}")


def ensure_account(uname, role, pw):
    st, d = req("POST", "/auth/login", body={"username": uname, "password": pw})
    if st == 200:
        return d.get("token", "")
    # 账号不存在：admin 创建后再次登录
    req("POST", "/users", admin_token,
        {"username": uname, "password": pw, "role": role, "display_name": "e2e-" + role})
    _created_users.add(uname)
    st, d = req("POST", "/auth/login", body={"username": uname, "password": pw})
    return d.get("token", "")


tokens = {}
for role, (u, p) in FIXTURES.items():
    tokens[role] = ensure_account(u, ROLE_OF[role], p)
    check(f"login:{role}", bool(tokens[role]), f"via={u}")

A, O, U, V = tokens["admin"], tokens["operator"], tokens["user"], tokens["viewer"]

# ── 1. cron shell 动作限 admin/operator ──
SCHED = {"schedule_type": "daily", "schedule_config": {"hour": 3, "minute": 0}}
st, d = req("POST", "/cron", U, {"name": "e2e_shell_u", "action": "shell", "payload": "echo hi", **SCHED})
check("cron-shell:user=403", st == 403, (st, d.get("detail", "")))
st, d = req("POST", "/cron", O, {"name": "e2e_shell_o", "action": "shell", "payload": "echo hi", **SCHED})
check("cron-shell:operator=200", st == 200, st)
op_cron_id = d.get("id") or (d.get("job") or {}).get("id")
st, d = req("POST", "/cron", U, {"name": "e2e_chat_u", "action": "chat", "payload": "你好", **SCHED})
check("cron-chat:user=200", st == 200, st)
u_cron_id = d.get("id") or (d.get("job") or {}).get("id")
# viewer 无 cron:write
st, _ = req("POST", "/cron", V, {"name": "e2e_v", "action": "chat", "payload": "x", **SCHED})
check("cron:viewer=403", st == 403, st)
# 清理
for tid, tk in ((op_cron_id, O), (u_cron_id, U)):
    if tid:
        req("DELETE", f"/cron/{tid}", tk)

# ── 2. 公共 Provider 仅 admin 可改/删 ──
st, d = req("GET", "/providers", O)
pubs = [p for p in d.get("providers", []) if not p.get("owner")]
check("providers:list:operator=200", st == 200, f"public={len(pubs)}")
if pubs:
    name = pubs[0]["provider"]
    st, d = req("PUT", f"/providers/{name}", O, {"priority": pubs[0].get("priority", 50)})
    check("provider-public:operator-put=403", st == 403, (st, d.get("detail", "")))
    st, d = req("DELETE", f"/providers/{name}", O)
    check("provider-public:operator-del=403", st == 403, st)
    st, d = req("PUT", f"/providers/{name}", A, {"priority": pubs[0].get("priority", 50)})
    check("provider-public:admin-put=200", st == 200, st)
# viewer 无 models:write
if pubs:
    st, _ = req("PUT", f"/providers/{pubs[0]['provider']}", V, {"priority": 50})
    check("provider:viewer-put=403", st == 403, st)

# ── 2b. 普通用户（user）有 models:write：可配置自己的专属 Provider ──
st, d = req("POST", "/providers", U, {
    "name": "e2e_u_prov", "base_url": "https://api.example.com/v1",
    "api_key": "sk-e2e-user", "models": ["my-model-a"]})
check("provider:user-post=200", st == 200, (st, d.get("detail", "")))
st, _ = req("PUT", "/providers/e2e_u_prov", U, {"priority": 99})
check("provider:user-put-own=200", st == 200, st)
st, d = req("POST", "/models/default", U, {"model": "e2e_u_prov/my-model-a"})
check("provider:user-set-default=200", st == 200, st)
# 但不能改/删公共 Provider（owner 隔离）
if pubs:
    st, _ = req("PUT", f"/providers/{pubs[0]['provider']}", U, {"priority": 50})
    check("provider:user-put-public=403", st == 403, st)
    st, _ = req("DELETE", f"/providers/{pubs[0]['provider']}", U)
    check("provider:user-del-public=403", st == 403, st)
# 不能通过同名 add 覆盖他人/公共 Provider（防越权覆盖）
if pubs:
    st, d = req("POST", "/providers", U, {
        "name": pubs[0]["provider"], "base_url": "https://evil.example.com/v1",
        "api_key": "x", "models": ["z"]})
    check("provider:user-takeover-blocked=400", st == 400, (st, d.get("detail", "")))
# 清理本人 Provider
req("DELETE", "/providers/e2e_u_prov", U)

# ── 3. /models/fetch SSRF 防护（私网地址默认拒绝） ──
st, d = req("POST", "/models/fetch", A, {"base_url": "http://127.0.0.1:8080/v1", "api_key": ""})
check("models-fetch:private=403", st == 403, (st, d.get("detail", "")))
st, d = req("POST", "/models/fetch", A, {"base_url": "http://169.254.169.254/v1", "api_key": ""})
check("models-fetch:metadata=403", st == 403, st)

# ── 4. 模块写门控（普通用户已有 modules:write，可建自己的模块） ──
st, _ = req("POST", "/mcp", V, {"name": "e2e_v_mcp", "command": "echo"})
check("mcp:viewer-post=403", st == 403, st)
st, _ = req("POST", "/mcp", U, {"name": "e2e_u_mcp", "command": "echo"})
check("mcp:user-post=200", st == 200, st)
req("DELETE", "/mcp/e2e_u_mcp", U)  # 清理本人创建的模块

# ── 5. 只读角色读取正常 ──
for path in ("/providers", "/cron", "/mcp", "/agents"):
    st, _ = req("GET", path, V)
    check(f"viewer-read:{path}=200", st == 200, st)

# 清理本次脚本创建的测试账号（预置账号不删除）
# 注意：后端删除端点为 DELETE /users/{uid}，路径参数是数字 ID 而非用户名，
# 必须先查 username -> id 映射，否则删除会静默失败、测试账号残留在生产实例上。
if _created_users:
    _st, _ul = req("GET", "/users", admin_token)
    _id_of = {u.get("username"): u.get("id") for u in (_ul.get("users") or [])}
    _left = []
    for uname in _created_users:
        uid = _id_of.get(uname)
        if uid is None:
            continue
        _dst, _ = req("DELETE", f"/users/{uid}", admin_token)
        if _dst != 200:
            _left.append(f"{uname}(id={uid},http={_dst})")
    check("cleanup:created-users", not _left, "removed=%d %s" % (
        len(_created_users) - len(_left), ("left=" + ",".join(_left)) if _left else ""))

print("\n==== RESULT: %d PASS / %d FAIL ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED:", FAIL)
    raise SystemExit(1)
