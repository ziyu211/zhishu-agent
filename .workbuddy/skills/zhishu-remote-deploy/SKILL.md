---
name: zhishu-remote-deploy
description: 将智枢（zhishu-agent）最新代码部署到远程 docker 主机并重建容器。当用户说「更新远程docker / 部署到服务器 / 更新线上实例 / 推到远程docker」时使用。覆盖 git archive → scp → 远程解压(保留 live 密钥) → docker build → 重建容器(保留数据卷+secret) → 验证 全链路。包含 scp 端口大写 -P、zhishu.yaml 被 gitignore 不进包、secret↔providers 强耦合等关键坑。
---

# 智枢远程 docker 部署

将本地 `D:\data\hemers\zhishu-agent` 的 HEAD 代码部署到远程主机并重建 `zsagent` 容器。

## 默认连接（部署前先用 known_hosts / ssh 配置核实，可能已变）
- 主机：`152.136.33.55`，SSH 端口：`6122`，用户：`root`
- 远程源码根：`/opt/zhishu-agent`
- 容器名：`zsagent`，对外端口 `8090→8080`（容器内 8080）
- 数据卷：`zsagent_data` → `/app/backend/data`（含 `zhishu_users.db` 与 `providers.json`）
- live 配置：`/opt/zhishu-agent/deploy/zhishu.yaml`（**chmod 600**，提供运行时 `security.secret`）
- 当前镜像标签演进：`zsagent:1.0.0 → 1.0.1 → 1.0.2 → 1.0.3 …`（每次部署自增，旧版留作回滚）

## 流程（在本地 Git Bash 执行）

### 1. 打包 HEAD（含预构建前端 static/，不含 deploy/zhishu.yaml）
```bash
cd /d/data/hemers/zhishu-agent
git archive --format=tar.gz -o zhishu-src.tar.gz HEAD
# 校验：static 文件应 ~44 个；deploy/zhishu.yaml 计数应为 0（被 gitignore）
tar tzf zhishu-src.tar.gz | grep -c 'backend/zhishu/static/'
tar tzf zhishu-src.tar.gz | grep -c 'deploy/zhishu.yaml$'
```

### 2. 上传（⚠️ scp 端口必须大写 `-P`，小写 `-p` 被当 preserve 且把端口当文件名 → 静默失败）
```bash
SCPOPTS="-P 6122 -i /c/Users/Administrator/.ssh/id_rsa -o StrictHostKeyChecking=yes -o BatchMode=yes"
scp $SCPOPTS zhishu-src.tar.gz root@152.136.33.55:/opt/zhishu-agent/zhishu-src.tar.gz
```

### 3. 远程解压（清理陈旧 tracked 源码，但 **保留 live zhishu.yaml**）
```bash
SSHOPTS="-p 6122 -i /c/Users/Administrator/.ssh/id_rsa -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o BatchMode=yes"
ssh $SSHOPTS root@152.136.33.55 'cd /opt/zhishu-agent && \
  rm -rf backend frontend scripts && \
  rm -f Dockerfile.local .dockerignore requirements.txt README.md build_zsagent.sh run-local.ps1 && \
  rm -f deploy/Dockerfile.local deploy/Dockerfile deploy/docker-compose.yml deploy/offline-build.sh deploy/zhishu.yaml.example && \
  tar xzf zhishu-src.tar.gz && echo EXTRACTED_OK && ls -la deploy/zhishu.yaml'
```

### 4. 远程构建新镜像（自增标签，如 1.0.3）
```bash
ssh $SSHOPTS root@152.136.33.55 'cd /opt/zhishu-agent && nohup docker build -t zsagent:1.0.3 -f deploy/Dockerfile.local . > build.log 2>&1 & echo started'
# 构建可能在 ssh 断开后仍在跑；判断完成以 `docker images | grep zsagent` 看到新镜像为准
ssh $SSHOPTS root@152.136.33.55 'sleep 12; docker images --format "{{.Repository}}:{{.Tag}}  {{.ID}}" | grep zsagent; tail -3 /opt/zhishu-agent/build.log'
```

### 5. 重建容器（**完全复用**旧 mounts / env / ports / restart，保留数据卷与 secret 挂载）
```bash
ssh $SSHOPTS root@152.136.33.55 'docker rm -f zsagent; \
  docker run -d --name zsagent --restart always -p 8090:8080 \
    -v zsagent_data:/app/backend/data \
    -v /opt/zhishu-agent/deploy/zhishu.yaml:/app/deploy/zhishu.yaml:ro \
    zsagent:1.0.3; sleep 8; \
  docker ps --format "{{.Names}}  {{.Image}}  {{.Status}}  {{.Ports}}" --filter name=zsagent'
```

### 6. 验证（health + secret↔providers 耦合未失效）
```bash
ssh $SSHOPTS root@152.136.33.55 'curl -s -m 10 http://localhost:8090/health; \
  docker exec -i zsagent python - <<PY
import sys, json, urllib.request
sys.path.insert(0,"/app/backend")
from zhishu.core.config import ZhishuConfig
from zhishu.core.security import AuthService
cfg = ZhishuConfig.load("/app/deploy/zhishu.yaml")
tok = AuthService(cfg.security, users=None)._token("admin","admin")
req=urllib.request.Request("http://127.0.0.1:8080/api/v1/providers", headers={"Authorization":"Bearer "+tok})
p=json.loads(urllib.request.urlopen(req, timeout=10).read())
print("PROVIDER_COUNT", len(p.get("providers", p if isinstance(p,list) else [])))
PY'
```

### 7. 清理
```bash
ssh $SSHOPTS root@152.136.33.55 'rm -f /opt/zhishu-agent/zhishu-src.tar.gz'
rm -f /d/data/hemers/zhishu-agent/zhishu-src.tar.gz   # 本地也删，保留远程 build.log
```

## 关键坑
1. **scp 端口大写 `-P`**：小写 `-p` = preserve 模式，会把 `6122` 当文件名 → 上传静默失败。
2. **zhishu.yaml 被 gitignore**：tar 包不含它，远程 live 配置不会被覆盖；切勿在清理步骤里 `rm deploy/zhishu.yaml`。
3. **secret ↔ providers 强耦合**：volume 内 `providers.json` 的 Key 用 `security.secret` 派生密钥加密。重建容器**只要挂载同一 live zhishu.yaml 且数据卷 intact**，Key 仍可解密（PROVIDER_COUNT>0 即证明）。若曾 `docker rm`+重建且**换了 secret 或丢了 volume**，providers 会解密失败 → 需按旧 secret 解出明文 Key 经 `PUT /api/v1/providers/{name}` 回填。
4. **登录 401 是配置漂移、非部署回归**：yaml 的 `security.admin_password` 仅在用户库为空时生效；线上登录走 volume `zhishu_users.db` 的密码哈希。真实管理员用库内口令登录；遗忘走重置流程。
5. **签名密钥硬闸门**：`enable_auth=true` 且 `security.secret=="change-me-zhishu-secret"` 且未设 `ZHISHU_ALLOW_INSECURE_DEFAULTS=1` → `main.py` 直接 `raise SystemExit(2)` 起不来。线上用强随机 secret（在 live zhishu.yaml 中），正常。
6. **多智能体路由是 opt-in**：自 commit `0dbf104` 起，普通问题/能力咨询不会触发多 Agent 协作，仅当用户显式点名团队（如「股票分析团队」「用Orchestrator」）或显式要求「多智能体协作」才走委派。回归测试见 `backend/tests/test_multiagent_e2e.py::test_classifier_conservative`。

## 前置校验（部署前建议）
- `git status` 干净或确认待部署改动已 commit（部署用的是 `git archive HEAD`）。
- 本地用系统 Python 3.11 跑 `tests/run_e2e.py --verbose` + `tests/test_closure_audit.py` 确认全绿（托管 3.13 缺 fastapi 会误报）。
