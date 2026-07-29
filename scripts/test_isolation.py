"""智枢 多用户隔离实测脚本（stdout 报告 + 自动清理）。

验证 #258(skills/plugins/mcp) 与 #262(agents) 的 owner 隔离：
- 普通用户只能看到/管理「共享 + 本人」；跨用户访问私有项 → 404；
- admin 可见全部，且可管理共享项。
"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:8080"
ADMIN = ("admin", "zhishu@2026")

results = []


def _req(method, path, token=None, body=None, raw=False):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ct = r.headers.get("Content-Type", "")
            if raw or "application/json" not in ct:
                return r.status, (r.read() if raw else r.read().decode("utf-8", "replace"))
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = e.read().decode("utf-8", "replace")
        return e.code, payload


def login(username, password):
    st, body = _req("POST", "/api/v1/auth/login", body={"username": username, "password": password})
    assert st == 200, f"login {username} failed: {st} {body}"
    return body["token"]


def check(label, cond, detail=""):
    results.append((label, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", label, ("" if cond else f"| {detail}"))


def main():
    admin_tok = login(*ADMIN)
    created_uids = []
    created_agents = []
    created_skills = []

    # 1) 创建两个测试用户
    ua_tok = ub_tok = None
    for uname in ("iso_a", "iso_b"):
        st, body = _req("POST", "/api/v1/users", token=admin_tok,
                        body={"username": uname, "password": "Iso@1234", "role": "operator"})
        if st == 200:
            created_uids.append(body["id"])
            if uname == "iso_a":
                ua_tok = login(uname, "Iso@1234")
            else:
                ub_tok = login(uname, "Iso@1234")
        else:
            raise SystemExit(f"create user {uname} failed: {st} {body}")
    check("创建测试用户 iso_a/iso_b", ua_tok and ub_tok)

    # 2) iso_a 创建私有 agent / skill
    st, body = _req("POST", "/api/v1/agents", token=ua_tok,
                    body={"name": "iso_a_agent", "description": "private", "enabled": True})
    check("iso_a 创建私有 agent", st == 200, f"{st} {body}")
    if st == 200:
        created_agents.append("iso_a_agent")

    st, body = _req("POST", "/api/v1/skills", token=ua_tok,
                    body={"name": "iso_a_skill", "content": "x"})
    check("iso_a 创建私有 skill", st == 200, f"{st} {body}")
    if st == 200:
        created_skills.append("iso_a_skill")

    # 3) admin 创建共享 agent / skill（owner=None）
    st, body = _req("POST", "/api/v1/agents", token=admin_tok,
                    body={"name": "shared_agent", "description": "shared", "enabled": True})
    check("admin 创建共享 agent", st == 200, f"{st} {body}")
    if st == 200:
        created_agents.append("shared_agent")

    st, body = _req("POST", "/api/v1/skills", token=admin_tok,
                    body={"name": "shared_skill", "content": "x"})
    check("admin 创建共享 skill", st == 200, f"{st} {body}")
    if st == 200:
        created_skills.append("shared_skill")

    # 4) 列表可见性
    def names_list(token, path):
        st, body = _req("GET", path, token=token)
        assert st == 200, f"list {path} failed {st} {body}"
        return [a["name"] for a in body.get("agents", body.get("skills", []))]

    a_agents = names_list(ua_tok, "/api/v1/agents")
    b_agents = names_list(ub_tok, "/api/v1/agents")
    adm_agents = names_list(admin_tok, "/api/v1/agents")
    a_skills = names_list(ua_tok, "/api/v1/skills")
    b_skills = names_list(ub_tok, "/api/v1/skills")

    check("iso_a 列表含 iso_a_agent", "iso_a_agent" in a_agents, str(a_agents))
    check("iso_a 列表含 shared_agent", "shared_agent" in a_agents, str(a_agents))
    check("iso_b 列表含 shared_agent", "shared_agent" in b_agents, str(b_agents))
    check("iso_b 列表不含 iso_a_agent", "iso_a_agent" not in b_agents, str(b_agents))
    check("admin 列表含 iso_a_agent", "iso_a_agent" in adm_agents, str(adm_agents))
    check("iso_a 技能列表含 iso_a_skill", "iso_a_skill" in a_skills, str(a_skills))
    check("iso_b 技能列表含 shared_skill", "shared_skill" in b_skills, str(b_skills))
    check("iso_b 技能列表不含 iso_a_skill", "iso_a_skill" not in b_skills, str(b_skills))

    # 5) 跨用户越权访问 → 404
    st, _ = _req("GET", "/api/v1/agents/iso_a_agent", token=ub_tok)
    check("iso_b GET iso_a 私有 agent → 404", st == 404, f"got {st}")
    st, _ = _req("DELETE", "/api/v1/agents/iso_a_agent", token=ub_tok)
    check("iso_b DELETE iso_a 私有 agent → 404", st == 404, f"got {st}")
    st, _ = _req("PUT", "/api/v1/agents/iso_a_agent/toggle", token=ub_tok, body={"enabled": False})
    check("iso_b TOGGLE iso_a 私有 agent → 404", st == 404, f"got {st}")
    st, _ = _req("GET", "/api/v1/skills/iso_a_skill", token=ub_tok)
    check("iso_b GET iso_a 私有 skill → 404", st == 404, f"got {st}")

    # 6) 合法可见性：iso_b 看共享项 200，iso_a 看自己 200，admin 看私有 200
    st, _ = _req("GET", "/api/v1/agents/shared_agent", token=ub_tok)
    check("iso_b GET 共享 agent → 200", st == 200, f"got {st}")
    st, _ = _req("GET", "/api/v1/agents/iso_a_agent", token=ua_tok)
    check("iso_a GET 自己 agent → 200", st == 200, f"got {st}")
    st, _ = _req("GET", "/api/v1/agents/iso_a_agent", token=admin_tok)
    check("admin GET iso_a 私有 agent → 200", st == 200, f"got {st}")

    # 7) 共享项非 admin 写操作 → 403
    st, _ = _req("DELETE", "/api/v1/agents/shared_agent", token=ub_tok)
    check("iso_b DELETE 共享 agent → 403", st == 403, f"got {st}")
    st, _ = _req("DELETE", "/api/v1/skills/shared_skill", token=ub_tok)
    check("iso_b DELETE 共享 skill → 403", st == 403, f"got {st}")

    # ---- 清理 ----
    print("\n--- cleanup ---")
    for n in created_agents:
        st, _ = _req("DELETE", f"/api/v1/agents/{n}", token=admin_tok)
        print(f"  delete agent {n}: {st}")
    for n in created_skills:
        st, _ = _req("DELETE", f"/api/v1/skills/{n}", token=admin_tok)
        print(f"  delete skill {n}: {st}")
    for uid in created_uids:
        st, _ = _req("DELETE", f"/api/v1/users/{uid}", token=admin_tok)
        print(f"  delete user #{uid}: {st}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n==== RESULT: {passed}/{len(results)} passed ====")
    if passed != len(results):
        raise SystemExit("ISOLATION TEST FAILED")


if __name__ == "__main__":
    main()
