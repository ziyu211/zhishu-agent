# 部署与验证 Playbook（zhishu-agent）

## 一、前端构建

```bash
cd /d/data/hemers/zhishu-agent/frontend
# 用受管 Node（绕过 vue-tsc 严格类型报错，esbuild 仍正常打包）
C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2/node.exe ./node_modules/vite/bin/vite.js build
```
产物落在 `backend/zhishu/static/`（Dockerfile 依赖预构建产物）。

## 二、热更新容器 zsagent（防目录嵌套陷阱）

```bash
# 1. 清旧 static（避免哈希残留）
docker exec zsagent rm -rf /app/backend/zhishu/static
# 2. 先删 api/core，再拷到父目录（关键：杜绝嵌套）
docker exec zsagent rm -rf /app/backend/zhishu/api /app/backend/zhishu/core
docker cp backend/zhishu/api   zsagent:/app/backend/zhishu/
docker cp backend/zhishu/core  zsagent:/app/backend/zhishu/
docker cp backend/zhishu/static zsagent:/app/backend/zhishu/static
# 单文件可直接覆盖：
docker cp backend/zhishu/main.py    zsagent:/app/backend/zhishu/main.py
docker cp backend/zhishu/context.py zsagent:/app/backend/zhishu/context.py
# 3. 重启并健康检查
docker restart zsagent
sleep 5
curl -s http://localhost:8080/api/v1/auth/status
```

> ⚠️ **docker cp 目录嵌套陷阱（曾踩坑）**：`docker cp backend/zhishu/api zsagent:/app/backend/zhishu/api`
> 会把源目录**嵌套**成 `/app/backend/zhishu/api/api/`，导致此前提交的后端修复根本没生效。
> 正确做法：先 `rm -rf` 目标，再 `docker cp <src> <container>:/app/backend/zhishu/`（父目录）。
> 单文件（main.py / context.py）直接 cp 覆盖则无此问题。

## 三、四角色验证

```bash
BASE=http://localhost:8080
ADMIN=$(curl -s -X POST $BASE/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"zhishu@2026"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 关键断言：
#  GET  /api/v1/settings              → admin 200，viewer 403
#  POST /api/v1/admin/redact {text}   → 200，手机号/邮箱被脱敏
#  POST /api/v1/auth/change-password {old_password,new_password} → 200（非 422）
```
- 临时建 `operator1` / `user1` / `viewer1` 跑权限矩阵后**务必删除**。
- viewer 对 conversations 写操作：因 `chat` 单权限且聊天需建会话，收紧会破坏 viewer 对话；当前按 owner 隔离（viewer 只能动自己会话），判为可接受。

## 四、提交规范

- 不提交 `backend/data/`（运行时数据：记忆库 / 身份文件），`.gitignore` 已处理。
- commit 风格：`fix(closure): ...` / `perf(optimize): ...` / `feat(rbac): ...`。
- 改完顺手在 `.workbuddy/memory/YYYY-MM-DD.md` 记一笔（尤其 docker cp 嵌套、断点目录）。
- 远程即 GitHub：`git push origin main`。
