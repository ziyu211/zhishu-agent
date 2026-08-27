# -*- coding: utf-8 -*-
"""v1.0.43 修复项 HTTP 冒烟（对运行中的本地实例执行）。"""
import hashlib, hmac, json, sys, time, urllib.request, urllib.error
BASE = "http://127.0.0.1:8080"
SECRET = "change-me-zhishu-secret"
PASS, FAIL = 0, []

def check(cond, name):
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}")

def sign(payload):
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def token(user="admin", role="admin"):
    p = json.dumps({"u": user, "r": role, "exp": int(time.time()) + 3600}, ensure_ascii=False)
    return f"{p}.{sign(p)}"

def call(method, path, tok=None, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw

T = token()
print("== A. 技能闭环：创建(中文名) -> 列表可见 -> 清理 ==")
st, r = call("POST", "/api/v1/skills", T, {"name": "写周报测试", "description": "冒烟测试", "version": "1.0.0", "content": "## 写周报\n每周五生成周报", "enabled": True})
check(st == 200 and isinstance(r, dict) and r.get("ok"), f"POST /skills 中文名创建 (status={st})")
st, r = call("GET", "/api/v1/skills", T)
names = [s.get("name") for s in ((r or {}).get("skills") or [])]
check(st == 200 and "写周报测试" in names, f"GET /skills 列表可见刚创建的技能 (共{len(names)}个)")
use = [s for s in ((r or {}).get("skills") or []) if s.get("name") == "写周报测试"]
check(bool(use) and "use_count" in (use[0] or {}), "列表返回 use_count 字段（技能用法统计）")
st, r = call("DELETE", "/api/v1/skills/" + urllib.parse.quote("写周报测试"), T)
check(st == 200, "清理测试技能")
st, r = call("GET", "/api/v1/skills", T)
check("写周报测试" not in [s.get("name") for s in ((r or {}).get("skills") or [])], "清理后列表不再包含")

print("== B. 对齐修复：向量记忆 / settings 开关 / revoke 可达 ==")
st, r = call("GET", "/api/v1/memory/vector", T)
check(st == 200 and isinstance(r, dict), f"GET /memory/vector 可访问 (status={st}, keys={list((r or {}).keys())[:4]})")
st, r = call("POST", "/api/v1/settings", T, {"security": {"code_exec_network_isolated": True}})
check(st == 200 and (r or {}).get("security", {}).get("code_exec_network_isolated") is True,
      f"POST /settings 保存 code_exec_network_isolated (status={st})")
st, r = call("POST", "/api/v1/settings", T, {"security": {"code_exec_network_isolated": False}})
check(st == 200, "恢复 code_exec_network_isolated=false")
st, r = call("POST", "/api/v1/users/999999/revoke", T)
check(st in (404, 400), f"POST /users/{{id}}/revoke 端点可达 (不存在用户→{st})")

print(f"\n结果：{PASS} 通过 / {len(FAIL)} 失败")
sys.exit(1 if FAIL else 0)
