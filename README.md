# 智枢智能体（Zhishu Agent）

> 一个面向**内网离线、安全合规、自主可控**场景的多用户本地智能体系统。
> 采用 **FastAPI 单进程**同时托管智能体引擎、REST/SSE API 与编译后的前端；配置驱动多模型接入，内置 RBAC 多租户、知识库、记忆、工具、插件/技能/MCP、定时任务与技能自进化闭环。
>
> 最近更新：2026-08-03 — 全量代码审计与架构加固（目录清理 · 业务流程梳理 · 漏洞修复 · 多用户架构评估，详见第十一节「安全审计与多用户架构」）。

---

## 一、设计目标

- **单进程部署**：一个 FastAPI 进程同时承担「Agent 引擎 + API 服务 + 静态前端托管」，无需独立的 Node BFF 或反向代理，镜像体积小、部署单元合一。
- **配置驱动的多模型接入**：LLM / Embedding 调用跟随 `deploy/zhishu.yaml` 与运行时 Provider 管理（`data/providers.json`），支持国产 OpenAI 兼容端点（通义千问、智谱 GLM、DeepSeek、Kimi、文心一言、MiniMax、讯飞星火）以及本地 Ollama / vLLM。
- **多用户与租户隔离**：内置四角色 RBAC（admin/operator/user/viewer），资源默认私有，支持「私有 / 全员共享 / 按角色共享」三级共享粒度，admin 可代管任意用户。
- **安全合规**：数据脱敏、审计日志、国密（SM4/SM3）凭据保护、出网隔离、SSRF 防护、媒体文件鉴权网关、弱密钥启动硬闸门。
- **可离线运行**：默认使用本地 SQLite 向量库与确定性哈希向量降级，无外部服务依赖即可启动。

---

## 二、整体架构

```
浏览器 ──(同源 fetch / SSE)──▶ Python(FastAPI 单进程)
                                  │
                                  ├── 智能体引擎（ReAct 循环 + 流式 SSE）
                                  ├── REST / SSE API（RBAC + 多租户隔离）
                                  ├── /media 鉴权网关（按 owner 隔离）
                                  └── 编译后的 Vue 静态前端（static/）
```

由 **FastAPI 单进程**统一托管：① Agent 推理引擎 ② REST/SSE API ③ 编译后的 Vue 静态资源。前端通过同源调用后端，开发期与生产期共用同一套契约，无 Node BFF。

---

## 三、RBAC 权限模型与多租户

### 3.1 角色权限矩阵（`core/security.py` ROLES）

| 权限点 | admin | operator（运维/配置） | user（普通用户） | viewer（只读访客） |
|--------|:-----:|:--------:|:----:|:------:|
| chat（对话） | ✅ | ✅ | ✅ | ✅ |
| knowledge:read / write | ✅ | ✅ / ✅ | ✅ / ✅ | ❌ |
| models:read / write | ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| modules:read / write（插件/技能/MCP/记忆） | ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| agents:read / write | ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| cron:read / write | ✅ | ✅ / ✅ | ✅ / ✅ | ✅ / ❌ |
| audit:read（审计） | ✅ | ✅ | ❌ | ❌ |
| users / settings / admin 端点 | ✅ | ❌ | ❌ | ❌ |

- `admin` 拥有通配 `*`；写权限隐含读权限；前端以 `app.can('<perm>')` 统一门控按钮/表单，无权限时展示「只读」徽标。
- **cron shell 动作**额外限制：仅 admin/operator 可创建/修改 `action=shell` 的定时任务（防任意命令执行）。

### 3.2 多租户隔离与共享

- 所有资源（Provider / 插件 / 技能 / MCP / Agents / 定时任务 / 知识文档 / 会话 / 媒体文件）带 `owner`，**默认私有**。
- 共享粒度三态（前端 `ShareScopeSelector`）：**私有** → **按角色共享**（`share_with: ["operator","user","viewer"]`）→ **全员共享**（`shared: true`）。
- `owner` 为空的资源视为「**公共**」：全员可见、**仅 admin 可改/删**。
- admin 可通过 `X-Act-As` 请求头（前端「切换用户」）代管任意用户的资源。
- 媒体文件按 `/media/<owner>/<file>` 存储，网关校验 Token 归属，禁止跨租户访问。

---

## 四、核心模块

### 1. LLM 接入层（配置驱动多 Provider + 回退链）
- **抽象 `LLMProvider`**：基于 `httpx` 自研轻量客户端，零境外 SDK 依赖（`core/providers/`，`core/llm.py` 为兼容 shim）。
- **多 Provider**：国产 OpenAI 兼容端点 + 本地推理（Ollama / vLLM）。
- **运行时 Provider 管理**：前端「模型」页支持增删改 Provider、探测模型（`/models/fetch`，带 SSRF 防护）、设默认模型、按共享范围下发；持久化至 `data/providers.json`（API Key 以 SM4/XOR 混淆落盘，接口只回掩码）。
- **故障回退链 + 负载均衡**：按可用性与密钥状态自动筛选，主用不可用时回退备用。
- **离线兜底**：未配置云端 Key 时可指向本地 Ollama。

### 2. 知识库与 RAG
- **向量库**：默认 `SQLite + numpy 余弦相似度`，零外部服务内网直用；`vector_store.py` 预留 Milvus / pgvector / 达梦(DM) 后端接口。
- **Embedding**：网络 Provider 端点或本地模型，批量入库；未配置时降级确定性哈希向量，检索流程不中断。
- **文档解析**：零依赖解析 PDF/Office 等（`parsers.py`），扫描件 PDF 支持页面图片预览；文档按 owner 隔离、可共享，删除共享文档仅 admin。
- **知识图谱**（`kgraph.py`）：从入库文档抽取实体关系，前端图谱可视化。

### 3. 记忆系统
- **会话记忆**：SQLite FTS5 存储多轮对话，跨会话全文检索。
- **长期记忆工具**（`memory`）：Agent 主动 `recall / save / update_user / forget`，持久化 `MEMORY.md / USER.md / SOUL.md`，下轮注入系统提示；设置页可开关（admin，持久化 `data/config.override.json`）。
- **跨会话回忆**（`session_search`）：关键词检索 / 指定会话翻阅 / 最近会话浏览，零 LLM 成本。

### 4. 工具系统（Toolset）
工具按能力分组（`toolsets`），按需启用：

| 工具分组 | 工具 | 说明 |
|----------|------|------|
| `terminal` | `terminal_run` | 沙箱内终端命令执行 |
| `file` | `file_read` / `file_write` / `file_list` | 文件读写（大文件分页 + 零依赖文档解析） |
| `knowledge` | `knowledge_search` / `knowledge_list` / `knowledge_read` | 知识库检索 |
| `web` | `safe_web_fetch` / `web_search` | 受控外网访问（`outbound_allow` 门控）；多搜索引擎后端 |
| `memory` | `memory` | 长期记忆读写 |
| `todo` | `todo` | 任务清单 |
| `sessions` | `session_search` | 跨会话回忆 |
| `delegate` | `delegate_to_agent` | 委派子代理 |
| `skills` | `read_skill` | 技能渐进披露 |
| `code_exec` | `code_exec` | 沙箱内代码执行 |

- **沙箱**：工具仅能在 `SANDBOX_ROOT`（默认 `data/sandbox`）内操作。
- **出网隔离**：`safe_web_fetch` / `web_search` 受 `security.outbound_allow` 门控。
- **多模态生成**：支持图片生成/视频生成路由（`image_routing.py`），产物经 `MediaStore` 按 owner 落盘、`/media` 网关鉴权访问。

### 5. 插件 / 技能 / MCP（运行时模块）
- **插件（Plugins）**：上传/编写 Python 插件即注册为工具，增删改/启停即时生效（`modules.py _sync_plugins`），支持共享范围。
- **技能（Skills）**：SKILL.md 技能包管理，导入/新建/编辑，Agent 按需渐进读取。
- **MCP 服务器**：配置 stdio MCP Server（command/args/env），连接后其工具注册为 `mcp__<server>__<tool>`，前端支持连接测试与工具调用测试。
- 三者均按 owner 隔离 + 三态共享，非 owner 且非 admin 只读。

### 6. 安全合规模块
- **数据脱敏**（`redact.py`）：手机号/身份证/邮箱/银行卡等正则遮蔽，审计落库前自动脱敏；系统页提供「脱敏自测」卡片（`/admin/redact`）。
- **国密与凭据保护**（`credentials.py` / `security.py`）：API Key 落盘 SM4 混淆（库缺失降级 XOR）；Token HMAC，可选 SM3；密码加盐哈希。
- **启动硬闸门**：`enable_auth=true` 且 `security.secret` 为默认值时**拒绝启动**（可用 `ZHISHU_ALLOW_INSECURE_DEFAULTS=1` 临时放行）；弱管理员口令启动告警。
- **SSRF 防护**：`/models/fetch` 默认拒绝内网/私有/环回地址（含云 metadata），需 `security.allow_private_fetch: true` 显式放开（本地 Ollama 探测场景）。
- **审计日志**：关键操作记录，脱敏后落库，operator 以上可查。
- **CORS**：`allow_credentials=False`，避免 `*` + credentials 危险组合。
- **上传防护**：知识库与对话附件接口改用 `core/upload.read_upload_limited` 分块读取，超过 100MB 返回 413，防止大文件打满内存/磁盘。
- **SSRF 扩面**：除 `/models/fetch` 外，`safe_web_fetch` 与插件 http 类工具统一经 `core/ssrf.guard_url` 拦截内网/私有/回环/云元数据地址。
- **SPA 静态兜底修复路径穿越**：`main.py` 的 SPA fallback 改用 `realpath` 归一化并校验目标仍位于 static 目录内，杜绝 `../` 越权读取 `data/providers.json` 等敏感文件。
- **前端存储型 XSS 修复**：`MarkdownRenderer.vue` 表格单元格统一走 `inline()` 转义后再 `v-html` 渲染，避免 Markdown 注入持久化跨会话执行。
- **脱敏 fail-closed**：`redact` / `redact_dict` 在异常时整体隐藏并记录日志，而非回退原文，避免 PII 泄露。

### 7. 上下文压缩
`context_engine.py` 超长对话自动截断/摘要/轮转，保证长会话可用。

### 8. 定时任务（Cron）
`cron.py` 内网合规调度器：`interval / daily / cron` 三种调度，`chat`（定时对话，按任务 owner 的身份与角色执行）与 `shell`（仅 admin/operator）两种动作；任务按 owner 隔离，viewer 只读。

### 9. 技能自进化闭环
- **技能蒸馏**（`modules/skills.maybe_learn`）：复杂任务完成后异步沉淀 SKILL.md。
- **后台记忆反思**（`maybe_reflect`）：每轮结束异步蒸馏用户事实（opt-in 默认关闭）。
- **会话内 nudge**：按配置频率提醒模型沉淀记忆/技能。

### 10. 多智能体协作
- **子代理委派**（`delegate_to_agent`）+ **自定义 Agents**（前端可建专属 Agent：提示词/模型/工具集，支持共享）。
- **MoA 多模型协作**（`core/agent/moa.py`）。

---

## 五、目录结构

```
zhishu-agent/
├── README.md                 # 本文档
├── requirements.txt          # 依赖清单（国内 pip 源可用）
├── backend/
│   ├── start_backend.py      # 启动入口（uvicorn 托管 create_app，默认 0.0.0.0:8080）
│   ├── tests/                # 单测与端到端脚本（含 e2e_roles_check.py 四角色验证）
│   └── zhishu/
│       ├── main.py           # FastAPI 应用工厂（引擎 + API + /media 网关 + 静态托管 + 启动自检）
│       ├── core/
│       │   ├── config.py     # YAML + 环境变量 + config.override.json 配置中心
│       │   ├── providers/    # Provider 客户端与回退链（llm.py 为兼容 shim）
│       │   ├── credentials.py# Provider 运行时存储（SM4/XOR 混淆，providers.json）
│       │   ├── embedding.py  # Embedding（网络 / 本地 / 哈希降级，批量）
│       │   ├── vector_store.py # 向量库（sqlite 实现 + 多后端接口）
│       │   ├── rag.py        # 知识库检索增强 + 扫描件预览
│       │   ├── kgraph.py     # 知识图谱抽取
│       │   ├── memory/       # 会话记忆（SQLite FTS5）
│       │   ├── modules/      # 插件/技能/MCP 运行时 + 自进化模块
│       │   ├── agent/        # Agent 循环（ReAct + SSE）+ moa.py
│       │   ├── agents_runtime.py # 子代理运行时
│       │   ├── security.py   # 鉴权 / RBAC(ROLES) / 用户库 / 审计
│       │   ├── redact.py     # 数据脱敏
│       │   ├── media.py      # 媒体存储（按 owner 目录隔离）
│       │   ├── cron.py       # 定时任务调度
│       │   ├── image_routing.py # 多模态生成路由
│       │   └── tools/        # 工具注册中心 + builtins/
│       ├── api/              # auth/chat/conversations/knowledge/models/modules/
│       │                     # agents/cron/users/settings/admin 共 12 路由
│       └── static/           # 编译后的前端资源（随仓库分发，构建后覆盖）
├── frontend/                 # Vue3 + Vite + Naive UI（同源调用后端）
│   └── src/ ...
└── deploy/
    ├── zhishu.yaml.example   # 示例配置（复制为 zhishu.yaml 后填密钥，真实配置不入库）
    ├── Dockerfile            # 标准容器化构建（node:18-alpine + python:3.11-slim）
    ├── Dockerfile.local      # 受限/离线网络专用（华为云 SWR 基础镜像 + 国内源）
    └── docker-compose.yml    # 一键编排（后端 + 可选本地 Ollama）
```

> 注：`backend/zhishu/static/` **随仓库分发**（便于离线部署直接运行）；`deploy/zhishu.yaml` 真实配置已被 .gitignore 忽略。

---

## 六、核心数据流

```
用户提问 → API(/api/v1/chat, SSE, RBAC + X-Act-As)
   → Agent 循环：
       1. 组装上下文：会话记忆(memory) + 知识库(rag) + 长期记忆(MEMORY.md)
       2. 调用 LLM（按用户可见 Provider 解析回退链）
       3. 解析 tool_calls → 工具注册中心执行（沙箱 / 审计 / 出网门控）
       4. 结果回填，循环直至 final_answer；媒体产物按 owner 落盘
   → 流式返回 token / tool_call / done 事件
   → 异步触发：技能蒸馏(maybe_learn) 与（可选）记忆反思(maybe_reflect)
```

---

## 七、快速开始

### 7.1 部署前准备

```bash
# 1) 复制配置模板（deploy/zhishu.yaml 已被 .gitignore 忽略，请勿提交真实密钥）
cp deploy/zhishu.yaml.example deploy/zhishu.yaml
# 编辑 deploy/zhishu.yaml：填入 Provider api_key、管理员密码，
# 并将 security.secret 改为强随机值（默认值会触发启动硬闸门，进程拒绝启动）

# 2) 安装后端依赖
cd backend && pip install -r ../requirements.txt -i https://mirrors.aliyun.com/pypi/simple
cd ..

# 3) （可选）重新构建前端：产物输出到 backend/zhishu/static
cd frontend && npm install && npm run build && cd ..
```

### 7.2 启动

```bash
cd backend && python start_backend.py
# 浏览器打开 http://127.0.0.1:8080
```

> **访问入口**：直接打开 **FastAPI 后端地址（默认 http://127.0.0.1:8080）**，后端同源托管前端页面与 `/api/v1/*` 接口。
> 不要通过 `npm run preview`（4173）访问——那是纯静态预览，没有后端 API。
> 默认账号 `admin` / `zhishu@2026`（生产必须修改 `deploy/zhishu.yaml` 的 `admin_password`）。

---

## 八、关键配置项（deploy/zhishu.yaml）

| 配置段 | 关键字段 | 说明 |
|--------|----------|------|
| `providers`（顶层） | `<name>: {base_url, api_key, models, priority, ...}` | 多 Provider 接入；运行时增改经 `/api/v1/providers` 持久化到 `data/providers.json` |
| `default_model` | — | 全局默认模型（`provider/model`；留空则按可用 Provider 自动解析） |
| `embedding` | `backend` / `embed_model` / `fallback_hash` | 网络端点或本地；未配置降级哈希向量 |
| `security` | `secret` / `admin_password` / `enable_auth` / `outbound_allow` / `enable_sm` / `allow_private_fetch` | 签名密钥（**必改**）、出网开关、国密开关、SSRF 放行开关 |
| `web_search` | `backend` | bing_cn / duckduckgo / tavily / bing |
| `agent` | `nudge_interval` / `reflection_enabled` / `skills_auto_learn` | 自进化相关开关 |
| `cron` | — | 定时任务调度配置 |
| `memory` | `vector_enabled` 等 | 长期记忆（语义检索需配置 Embedding，无则优雅降级） |

完整字段见 `deploy/zhishu.yaml.example`；运行时覆盖项落盘 `data/config.override.json`（设置页）。

> ⚠️ **密钥耦合提示**：`data/providers.json` 中的 API Key 用 `security.secret` 派生密钥加密。**轮换 secret 会使已存 Key 失效**（对话报「所有 Provider 均不可用」）——轮换后需在前端「模型」页重新填入各 Provider 的 Key。

---

## 九、Docker 容器化部署

镜像为「单进程」设计：一个容器同时跑 Agent 引擎 + API + 前端，对外仅暴露 `8080`。

### 9.1 准备配置

```bash
cp deploy/zhishu.yaml.example deploy/zhishu.yaml
# 填入 api_key、管理员密码，并把 security.secret 改为强随机值
```

### 9.2 构建镜像

```bash
# 标准（需 docker.io 可达）
docker build -t zsagent:1.0.0 -f deploy/Dockerfile .

# 受限网络 / 国内零代理（推荐内网、本机）
docker build -t zsagent:1.0.0 -f deploy/Dockerfile.local .
```

### 9.3 运行

```bash
docker run -d --name zsagent \
  -p 8080:8080 \
  -v zsagent_data:/app/backend/data \
  --restart unless-stopped \
  zsagent:1.0.0
```

- `zsagent_data` 卷持久化：向量库、会话记忆、`providers.json`、用户库、媒体文件。
- 打开 http://localhost:8080 ，默认 `admin` / `zhishu@2026`（生产必改）。

### 9.4 配置不烧进镜像（推荐生产）

```bash
docker run -d --name zsagent -p 8080:8080 \
  -v "$(pwd)/deploy/zhishu.yaml:/app/deploy/zhishu.yaml:ro" \
  -v zsagent_data:/app/backend/data \
  --restart unless-stopped zsagent:1.0.0
```

> 重建容器（`docker rm` + `run`）会丢弃可写层：请确保挂载的 `zhishu.yaml` 中 `security.secret` 与旧值一致（否则触发硬闸门或 Provider Key 失效），或启动时传 `-e ZHISHU_SECRET=<同值>`。

### 9.5 一键编排

```bash
docker compose -f deploy/docker-compose.yml up -d
```

### 9.6 健康检查、验证与排错

```bash
curl http://localhost:8080/health        # 期望 {"status":"ok",...}
docker logs -f zsagent                    # 启动 / 对话日志
python backend/tests/e2e_roles_check.py   # 四角色 RBAC / 安全闭环端到端验证

# 同一套脚本可直接验证任意远程实例（BASE 与 admin 口令用环境变量覆盖）
ZHISHU_BASE=http://127.0.0.1:8090 ZHISHU_ADMIN_P='<远程admin密码>' python3 backend/tests/e2e_roles_check.py
```

脚本是**自包含**的：缺失的 `rs_op / rs_user / rs_viewer` 测试账号由 admin 自动创建，跑完按用户 **ID** 删除并以 `cleanup:created-users` 断言校验，不会在生产实例上留下测试账号。
断言条数随环境浮动（公共 Provider 为 0 时会跳过 4 条公共 Provider 保护断言），以 `0 FAIL` 为通过标准。

- 对话报「所有 LLM Provider 均不可用 / 401 / 429」：Key 失效、配额耗尽或 secret 轮换导致解密失败——在前端「模型」页重填 Key。
- 进程启动即退出（exit 2）：`security.secret` 仍为默认值，改为强随机值后重启。
- 普通用户报「Provider 未配置或未启用」：将 Provider 设为公共（owner 置空）或按角色共享。

### 9.7 远程服务器部署（实录流程）

将整套代码部署到一台远程 Docker 主机，全流程如下（宿主机侧执行）：

```bash
# 1. 打包干净源码（含预构建的 backend/zhishu/static，远程无需 Node 环境）
git archive --format=tar.gz -o zhishu-src.tar.gz HEAD

# 2. 上传并解压
ssh -p <port> root@<host> "mkdir -p /opt/zhishu-agent"
scp -P <port> zhishu-src.tar.gz root@<host>:/opt/zhishu-agent/
ssh -p <port> root@<host> "cd /opt/zhishu-agent && tar xzf zhishu-src.tar.gz && rm -f zhishu-src.tar.gz"

# 3. 远程构建镜像（耗时较长，建议 nohup 后台跑并 tail 日志）
ssh -p <port> root@<host> "cd /opt/zhishu-agent && nohup docker build -t zsagent:1.0.0 -f deploy/Dockerfile.local . > build.log 2>&1 &"

# 4. 生成生产配置：必须替换默认 secret 与 admin 口令，否则进程启动即 exit 2
#    secret 用 64 位强随机；配置以只读挂载进容器，不烧进镜像
cp deploy/zhishu.yaml.example deploy/zhishu.yaml && chmod 600 deploy/zhishu.yaml
#    编辑 security.secret 与 auth.admin_password

# 5. 启动（数据落 named volume，随宿主重启自恢复）
docker volume create zsagent_data
docker run -d --name zsagent --restart always \
  -p 8090:8080 \
  -v zsagent_data:/app/backend/data \
  -v /opt/zhishu-agent/deploy/zhishu.yaml:/app/deploy/zhishu.yaml:ro \
  zsagent:1.0.0

# 6. 回填 Provider Key（Key 与 secret 强耦合，不能直接拷贝旧密文，必须走 API 明文回填）
curl -X POST http://127.0.0.1:8090/api/v1/providers -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"name":"<provider>","base_url":"...","api_key":"...","models":["..."],"shared":true}'
curl -X POST http://127.0.0.1:8090/api/v1/models/default -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -d '{"model":"<provider>/<model>"}'
```

**部署踩坑提示**

- **Provider Key 不可随卷迁移**：`data/providers.json` 中的 Key 以 `security.secret` 派生密钥混淆落盘。新实例 secret 不同，直接搬运密文会解密失败。正确做法是用明文经 `POST/PUT /api/v1/providers` 回填，由目标实例用自己的 secret 重新加密。
- **端口不通先分清是谁在拦**：宿主 `firewall-cmd --list-ports` 已放行、`ss -lntp` 也在监听，但公网仍超时，基本可判定是**云厂商安全组**未放行，需到云控制台开端口。
- **未知 `/api/v1/*` 路径会被 SPA 兜底返回 200 + `index.html`**，排障时别把 HTML 响应误判成接口连通。

---

## 十、模块功能闭环清单（本次全量审计）

下列每个模块均已完成「后端 CRUD + RBAC 鉴权 + owner 隔离 / 三态共享 + 前端视图与写门控 + 持久化」的完整闭环，并经 `backend/tests/e2e_roles_check.py` 四角色端到端验证（**18/18 PASS**）。

| 模块 | 后端端点（`api/`） | 权限点 | 隔离 / 共享 | 前端视图 | 持久化 |
|------|--------------------|--------|-------------|----------|--------|
| 认证 / 用户 | `/auth/*`、`/users/*` | `users:read/write`（仅 admin） | 用户库按账户隔离 | LoginView / UsersView | `zhishu_users.db`（SQLite） |
| 对话 / 会话 | `/chat`、`/conversations` | `chat` | 会话按 `owner`；admin 可切「全部 / 我的」 | ChatView + chat store | `zhishu_conversations.db` |
| 知识库 / RAG | `/knowledge` | `knowledge:read/write` | 文档按 `owner`，`owner` 空=公共可见 | KnowledgeView | 向量库 SQLite |
| 模型 / Provider | `/models`、`/providers` | `models:read/write` | `owner` 空=公共（仅 admin 改/删）；无 Key 云端 Provider 前端自动过滤 | ModelsView（ProvidersPanel / ProviderCard） | `data/providers.json`（Key SM4/XOR 混淆） |
| 插件 | `/modules/plugins` | `modules:read/write` | 默认私有；私有 / 角色 / 公共三态；非 owner 且非 admin 只读 | PluginsView | `data/plugins/<name>` |
| 技能 | `/modules/skills` | `modules:read/write` | 同上 | SkillsView | `data/skills/<name>` |
| MCP | `/mcp` | `modules:read/write` | 同上（含连接测试 / 工具调用测试） | McpView | `data/mcp/<name>` |
| 智能体 | `/agents` | `agents:read/write` | 同上；普通用户仅可编辑**本人所拥有**的智能体 | AgentsView | `data/agents` |
| 定时任务 | `/cron` | `cron:read/write` | 任务按 `owner`；`shell` 动作仅 admin/operator | CronView + cron store | `data/cron` |
| 设置 | `/settings` | `admin` | 长期记忆开关等运行时覆盖 | SettingsView | `data/config.override.json` |
| 系统 / 审计 / 脱敏 | `/admin/*` | `system:read` / `audit:read` | 审计脱敏后落库 | SystemView | 审计库 |
| 记忆 | `/modules/memory` | `modules:read/write` | 长期记忆按用户 | MemoryView | `MEMORY.md` / `USER.md` / `SOUL.md` |

**本轮重点修复（2026-07-30）**：
- 修正技能 / 插件 / MCP / 智能体视图按 `owner` 判定「可编辑」的字段取值（使用 `app.user.user` 而非未定义的 `app.user.username`），**普通用户现已能正常编辑 / 删除自己创建的模块**。
- 管理员「切换用户」(X-Act-As) 后，侧边栏模型选择器随身份同步刷新，避免与普通用户视角下聊天页模型列表不一致。
- 补齐 `Provider` 类型契约（`has_key` / `api_key_masked` / `priority` / `builtin`），移除与运行时契约冲突的过期 `CronJob` 重复类型。
- 后端 RBAC 矩阵对 admin 专属端点（`users` / `system` / `admin` / `settings`）补充显式声明，提升权限可读性（`*` 仍兜底，功能不受影响）。
- 端到端验证脚本改为**自包含**：缺失的四角色测试账号由 admin 自动创建并在结束后清理，可对任意新容器重复运行。

**已知设计决策**：知识库文档共享目前采用「`owner` 空=公共」单一维度（与其他模块「公共」概念一致），未实现「按角色共享」三级粒度；如需可按 `share_with` 模式扩展 `vector_store` 文档表。

---

## 十一、安全审计与多用户架构（2026-08-03）

### 11.1 全量代码审计与漏洞修复

对 `backend/zhishu/` 与 `frontend/src/` 做了静态全量审计（AST 扫描 + 人工走查 + 容器内单元/集成验证），本轮修复如下：

| 编号 | 风险 | 位置 | 处置 |
|------|------|------|------|
| V1 | 前端存储型 XSS（Markdown 表格单元格未转义即 `v-html`） | `frontend/.../MarkdownRenderer.vue` | 单元格内容统一走 `inline()` 转义 |
| V2 | SPA fallback 路径穿越（`../` 越权读 `data/providers.json` 等） | `backend/zhishu/main.py` | 改用 `realpath` 归一化并校验位于 static 目录内 |
| V3 | 国密缺失时密钥降级 XOR 混淆（弱加密） | `core/security.py` | 启动时打印安全告警，建议安装 `gmssl` |
| V4 | 多 worker 破坏进程内单例（限流/配额/定时任务重复） | `main.py` | 启动时对 `workers>1` 打印架构告警，建议 `workers=1` |
| V5 | 上传无大小上限（全量 `file.read()` 打满内存） | `api/knowledge.py` / `api/chat.py` | 新增 `core/upload.read_upload_limited` 分块读取，超 100MB 返回 413 |
| V7 | `safe_web_fetch` / 插件 http 无内网 IP 过滤 | `core/tools/builtins/web.py` / `core/modules/plugins.py` | 统一接入 `core/ssrf.guard_url` 拦截内网/私有/回环/云元数据 |
| V9 | 脱敏失败 fail-open 回退原文（可能泄露 PII） | `core/redact.py` | 改为 fail-closed，异常时整体隐藏并记录日志 |

V6（插件 shell 命令注入）、V8（operator 任意 shell/cron）属设计性 RCE，已通过角色闸门（`TOOL_MIN_ROLE` 要求 operator、`action=shell` 仅 admin/operator 可建）与出网/沙箱约束收敛；生产建议默认关闭 `allow_code_exec` 并对 shell 类动作加强审计。V10（默认密钥/口令）已由启动硬闸门与告警覆盖。

### 11.2 多用户架构与 FastAPI 适配性分析

**结论：FastAPI（ASGI）本身非常适合多用户并发场景；问题不在框架，而在当前「单进程 + 进程内可变单例」的状态管理方式。**

- **适配性优势**：FastAPI 基于 Starlette/asyncio，对 I/O 密集（LLM 流式、工具调用、文件落盘）的多用户并发吞吐表现良好；本项目的 RBAC、`owner` 归属、媒体网关、审计均已按多用户设计。
- **当前约束（单进程状态）**：`AppContext`、`ToolRegistry`、`ConcurrencyLimiter`、`CronScheduler`、SQLite 连接与对话缓存均为**进程内单例**。在 `workers=1` 下完全正确；若将 `workers` 设 >1 或横向扩多副本，各进程状态独立，会导致限流/配额/定时任务行为不一致、工具注册表漂移。
- **并发安全**：任务级身份通过 `contextvars` 透传 + `ToolContext.for_run()` 浅拷贝隔离，避免跨请求串号；`code_exec` 经 `asyncio` 子进程 + `RLIMIT_AS` + 超时强杀，不阻塞事件循环。
- **推荐部署形态**：
  - **默认/内网离线（本项目定位）**：单进程 `workers=1` + 进程内状态，简单可靠、数据不出本机，契合「自主可控」。
  - **更大规模**：将限流器/配额外置到 Redis、对话/审计落到共享数据库、定时任务由单进程协调（分布式锁），再用多容器 + 负载均衡水平扩展。本项目已为这一步预留了 `init_limiter` / `CronScheduler` 等注入点。

### 11.3 端到端业务流程

完整链路：浏览器同源调用 → 登录鉴权（HMAC Token + 四角色 RBAC + X-Act-As 代管）→ 对话入口（SSE 流式、owner 隔离）→ Agent ReAct 循环（上下文组装 / LLM 多 Provider 回退 / 解析 tool_calls）→ 工具执行层（沙箱路径隔离、审计、出网门控、SSRF 内网拦截、代码执行 RLIMIT+超时）→ 结果回填与思维图谱 → 异步闭环（技能蒸馏、记忆反思、定时任务、多智能体委派）。多用户通过 `owner` 归属 + `contextvars` 隔离 + 浅拷贝 + 命名空间保证彼此不可越权。

### 11.4 目录清理（本轮）

移除仓库根目录与 `backend/` 下遗留的调试/临时产物（`verify_*.py`、`_*.py` 测试脚本、`*.log`、`_cell_graph_preview.html` 等）与未使用的 `.venv-test`，不触及 `data/`（运行时数据）、`.venv/`、`frontend/node_modules/`、`backend/zhishu/static/`（构建产物）。

## 十二、全链路闭环审计与修复（2026-08-03）

在第十一节安全审计之上，本轮对**后端 core / 后端 API / 数据与持久化层 / 前端**四大面做了逐模块业务闭环走查，重点排查「入口 → 处理 → 持久化 → 反馈」链路上的断点（数据被静默破坏、资源不回收、状态不一致、删除不级联等）。

### 12.1 已修复的闭环断点

| 编号 | 断点 | 位置 | 影响 | 处置 |
|------|------|------|------|------|
| C1 | 对话部分更新把未传字段写成 `"null"` | `core/conversations.py` | 只改标题会清空全部消息，列表接口 `len(None)` 抛 500 | `update` 跳过 `None` 字段；`_row` 对反序列化结果做 `isinstance(list)` 兜底 |
| C2 | 轮换 `security.secret` 后 Provider 密钥被静默清空 | `core/credentials.py` | 全部 LLM 调用 401，且原密文不可恢复 | 新增 `_raw` 原始密文缓存，明文解不出时**保留原密文**而非写空 |
| C3 | 令牌无吊销机制 | `core/security.py` | 用户删除/停用/降级后旧令牌仍有效至 7 天 | `verify()` 回查用户库：不存在或非 active 拒绝；角色以库中当前值为准（无用户库的引导期/离线模式跳过，避免锁死） |
| C4 | 委派兜底 handler 丢失递归深度与角色 | `core/tools/builtins/delegate.py` | 子智能体可无限递归委派、越权使用工具 | 补齐 `delegate_depth` / `user_role` / `is_admin` 透传 |
| C5 | 内置工具自发现静默吞异常 | `core/tools/registry.py` | 单个 builtins 模块导入失败 → 工具全量消失且永不重试 | 改 fail-loud：记录 exception 日志，失败时不置 `_discovered`，允许后续重试 |
| C6 | 并发信号量取消时许可泄漏 | `core/concurrency.py` | 请求被取消后全局许可永久丢失，最终全站死锁 | 获取过程包裹 try/except，异常时回滚已获取许可再抛出 |
| C7 | cron 对 `sqlite3.Row` 调 `.get()` | `core/cron.py` | 定时对话任务角色回查必抛 `AttributeError` | 改为 `dict(row).get("role")` |
| C8 | 删除对话不级联清理 | `api/conversations.py` | 记忆 turns 与附件成为孤儿，长期膨胀 | 删除时清理 `memory` 中 `{owner}:{cid}` 前缀 turns + `attachments/<owner>/<cid>` 目录 |
| C9 | 删除用户不级联清理 | `api/users.py` | 对话/Provider/记忆/定时任务/知识库文档/本地目录全部残留 | 重写为「删用户 → 级联清理六类资源 → 审计埋点」 |
| C10 | 记忆前缀删除未转义通配符 | `core/memory/sqlite_provider.py` | 用户名含 `_`/`%` 会误删他人数据 | 新增 `clear_session_prefix`，`LIKE ... ESCAPE '\'` 转义 |
| C11 | `SkillsView`/`PluginsView` 未导入 `NTag` | `frontend/src/views/` | naive-ui 未全局注册，只读模式标签渲染失败 | 补齐 import |
| C12 | `streamChat` 绕过统一 `request()`，不带 `X-Act-As` | `frontend/src/api/chat.ts` | admin 代管他人时对话仍以自己身份发出 | 发送前注入 `X-Act-As` 头 |
| C13 | 设置/系统接口缺 `skipActAs` | `frontend/src/api/settings.ts`、`system.ts` | 代管态下读回的是被代管者的系统状态 | 管理端接口统一加 `skipActAs: true` |
| C14 | `ShareScopeSelector` 选「按角色共享」被弹回「私有」 | `frontend/src/components/modules/` | 角色级共享无法保存 | 加 `internal` 标志位打断 props↔state 回环 |
| C15 | `AgentsView` 缺 `sub_agents` 字段 | `frontend/src/views/AgentsView.vue` | 协调者无法在 UI 上配置成员智能体 | 表单新增「成员智能体（协调者）」多选项，读写全链路补齐 |

### 12.2 回归验证

新增两套可复现的验证脚本，均在运行容器 `zsagent` 内执行：

- `backend/tests/test_closure_audit.py` — 模块级闭环回归，**75/75 通过**（覆盖 C1–C15、令牌吊销引导期兼容，以及本轮新增的 #339 多智能体身份/工具裁剪、#340 共享连接池、#338 Shell 闸门、#341 向量签名隔离、#342 context_length 接入）。
- `backend/tests/http_closure_check.py` — 真实 HTTP 全链路验证，**24/24 通过**（对话部分更新不破坏消息、列表接口不再 500、删除级联生效、伪造但签名合法的令牌被 401 拒绝、协作工具已注册、#338 operator shell 任务拦截 `cat /etc/shadow` 且 `echo` 正常执行不泄露密钥、#341 `/knowledge/stats` 返回 `embedding_signature` 与 `stale`）。
- 既有 `backend/tests/test_multiagent_e2e.py` **6/6 通过**（含委派路由 A/B 与超时熔断），确认无回归。
- 真实对话冒烟：容器内 SSE 请求 `/api/v1/chat`，回复「连接正常」，证明 #340 共享连接池在真实流式场景下工作正常。

### 12.3 深度审计闭环（#338–#342，原遗留观察已全部闭环）

上一轮 12.3 列出的 5 条遗留观察，本轮已逐条完成代码闭环 + 回归验证：

| 编号 | 原遗留观察 | 处置 | 关键改动 |
|------|-----------|------|----------|
| #339 | `moa.py` 绕过 `filter_tool_specs` 且使用伪身份 `user="moa"` | 多智能体统一走 `contextvars` 真实身份 + `ToolContext.for_run()` 浅派生 + `filter_tool_specs` 裁剪，杜绝伪身份越权 | `core/agent/moa.py`、`core/agent/context_engine.py`、`core/agent/agent.py` |
| #340 | 每请求新建 `LLMClient` 且 `aclose()` 无调用点，连接池依赖 GC | 改为**全局共享连接池单例** `get_shared_http()`（双重检查加锁 + `is_closed` 惰性重建）；`main.py` lifespan teardown 显式回收 HTTP/cron/MCP 三处资源 | `core/providers/client.py`、`main.py` |
| #338 | cron shell 类任务在宿主进程裸跑，无沙箱 | 新增 `core/shellguard.py` **纵深防御闸门**：高危正则拒绝清单 + 可执行白名单（按 shell 控制算符切段校验首 token）+ 禁命令/进程替换 + 禁环境变量前缀 + 最小化子进程环境（剔除 `ZHISHU_*`/`SECRET`/`TOKEN`/`KEY` 类变量）+ 独立进程组（setsid/CREATE_NEW_PROCESS_GROUP）+ POSIX rlimit（AS/CPU/FSIZE/NPROC）+ 超时整组击杀；cron 与 terminal 工具接入 `check_command` + `run_guarded`；执行期实时回查角色（admin/operator），用户降级后旧任务即停 | `core/shellguard.py`（新增）、`core/cron.py`、`core/tools/builtins/terminal.py`、`core/config.py`（`allow_shell`/`shell_enforce_allowlist`/`shell_allowlist`/`shell_timeout`/`shell_mem_limit_mb`） |
| #341 | Embedding 降级 hash 向量与真实语义向量混入同一库 | **向量空间签名隔离**：每批向量随实际后端打 `emb_sig`（如 `hash:512` / `provider:qwen:text-embedding-v3:1024`），检索只与同签名向量比较 + 维度守卫防 `np.dot` 崩溃；`strict_ingest=True` 时降级入库直接报错；`signature_stats()` 暴露陈旧分块 | `core/embedding.py`、`core/vector_store.py`、`core/rag.py`、`core/config.py`（`strict_ingest`） |
| #342 | 对话接口 `context_length` 入参被静默丢弃 | 入参接入上下文组装链路，按模型/用户真实上下文窗口截断，未填不臆造、未知模型返回 `None` | `api/models.py`、`core/agent/context_engine.py` 等 |

### 12.4 安全配置建议（生产部署）

- **Shell 类能力**：默认 `allow_shell=true` 仅用于内网可信运维；生产建议将 `shell_enforce_allowlist=true` 保留，并显式收窄 `shell_allowlist`（白名单刻意不含 `sh/bash/cmd/powershell/env/xargs/eval/sudo/chmod/ssh/nc/docker/crontab` 等提权/横向移动工具）。
- **向量库**：保持 `strict_ingest=true`，避免 hash 伪向量污染真实语义检索；跨模型/跨 Provider 检索通过 `emb_sig` 自动隔离。
- **连接池**：`workers=1` 下单例共享池零开销；若未来扩多进程，需配合外部状态后端（见 11.2），避免每进程独立池。

> 本系统为「**完整可运行的多用户智能体平台**」：RBAC 多租户、模块共享、运行时 Provider 管理、安全防护与多用户并发隔离均已实现闭环，可直接二次开发接入具体模型与数据库。

### 12.5 CI e2e 失败根因与修复（2026-08-04）

GitHub Actions `E2E Tests / e2e`（`.github/workflows/e2e.yml`，push/PR 到 `main` 时执行 `python tests/run_e2e.py --verbose`，env `ZHISHU_ALLOW_INSECURE_DEFAULTS=1`）在 commit `cec5f1f1`（「深度审计闭环 #338–#342」）引入回归，自 run 3 起全部失败（"Failed in 23 seconds"）。根因均为 **asyncio 生命周期缺陷，且只在关停阶段触发**，故表现为 `TestClient` 关停即抛 `CancelledError`——任一 e2e 套件 FAIL 即令 job 失败。

| 根因 | 位置 | 表象 | 修复 |
|------|------|------|------|
| `cron.stop()` 用 `except Exception` 捕获 `await self._task`；但 `await` 一个「已被自己 `cancel()`」的任务必抛 `asyncio.CancelledError`（继承自 `BaseException`，`except Exception` 抓不住）→ 异常冲出 `stop()` → 冲出 `lifespan` teardown → ASGI 判定 lifespan 被取消 → 关停报 `CancelledError`，且排在 `cron.stop()` 之后的 MCP 回收被整段跳过 | `core/cron.py` | `test_chat_http_e2e.py` 在 `TestClient.__exit__` 关停时抛 `CancelledError`，HTTP 套件 FAIL | `stop()` 改为 `except (asyncio.CancelledError, Exception)` |
| `main.lifespan` 关停期三步资源回收（共享连接池 / cron / MCP 客户端）均用 `except Exception`，且未持有启动期后台任务引用——GC 可能提前回收，或在任务取消时泄漏 | `main.py` | 同上，且关停阶段资源泄漏 | 每步改为 `except (asyncio.CancelledError, Exception)` 自包含；新增 `_boot_tasks` 列表持有启动期任务引用，teardown 对未完成任务统一 `cancel()` + `await` 回收 |
| HTTP e2e 桩 `FakeLLM.chat` 缺 `tool_choice` 形参，而 `LLMClient.chat` 已新增该参数 → 真实调用抛 `TypeError` 被兜底成 error 事件，表象为「回复缺失」 | `tests/test_chat_http_e2e.py` | 自签 token 全链路对话用例失效 | 补 `tool_choice=None, **_kw` 兼容新签名 |
| 向量库路径 `RagConfig.path="data/zhishu_vector.db"` 为硬编码相对 CWD 裸路径，不跟随 `server.data_dir`，测试无法用临时目录隔离 → 串到仓库真实数据（本地曾因 17.8GB 真实向量库导致锁等待超时） | `core/rag.py` + `core/config.py` | 本地/多实例测试串数据、偶发 `database is locked` | `KnowledgeBase` 新增 `_resolve_store_path`：绝对路径原样保留，相对路径去掉重复 `data/` 前缀后拼到 `data_dir` 下；默认配置落点零迁移 |

附带清理：`backend/` 下调试/临时产物（`_diag_*.py`、`_dbg_*.txt`、`_e2e_run.txt`）已删除，不入库。

修复后验证（与 CI 干净环境等价复跑）：

- `python tests/run_e2e.py --verbose`（env `ZHISHU_ALLOW_INSECURE_DEFAULTS=1`）：`test_multiagent_e2e.py` **6/6 PASS** + `test_chat_http_e2e.py` **A/B/C 3 项 PASS** → **ALL E2E SUITES PASSED**。
- `backend/tests/test_closure_audit.py`：新增 3 个针对本根因的回归测试（`test_cron_stop_no_cancelled_error` / `test_lifespan_teardown_isolated` / `test_vector_store_follows_data_dir`），**89/89 断言通过**。
- 既有 `backend/tests/http_closure_check.py` 24/24 不受影响。
- 复查补充：`core/modules/mcp.py` 的 `MCPClient.close()` 取消 `_reader_task` 后未 `await`（同类隐患，未触发 e2e 因测试不连接 MCP client），已补 `await` + `except (asyncio.CancelledError, Exception)` 与 `cron.stop` 一致。
