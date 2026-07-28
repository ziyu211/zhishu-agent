# 智枢智能体（Zhishu Agent）

> 一个面向**内网离线、安全合规、自主可控**场景的本地智能体系统。
> 采用 **FastAPI 单进程**同时托管智能体引擎、REST/SSE API 与编译后的前端，配置驱动多模型接入，内置知识库、记忆、工具、定时任务与技能自进化闭环。

---

## 一、设计目标

- **单进程部署**：一个 FastAPI 进程同时承担「Agent 引擎 + API 服务 + 静态前端托管」，无需独立的 Node BFF 或反向代理，镜像体积小、部署单元合一。
- **配置驱动的多模型接入**：所有 LLM / Embedding 调用严格跟随 `deploy/zhishu.yaml` 配置，支持国产 OpenAI 兼容端点（通义千问、智谱 GLM、DeepSeek、Kimi、文心一言、MiniMax、讯飞星火）以及本地 Ollama / vLLM。
- **安全合规**：内置数据脱敏、审计日志、RBAC 权限、国密（SM4/SM3）凭据保护、私网隔离开关（工具默认禁止出网，白名单放行）。
- **可离线运行**：默认使用本地 SQLite 向量库与确定性哈希向量降级，无外部服务依赖即可启动。

---

## 二、整体架构

```
浏览器 ──(同源 fetch / SSE)──▶ Python(FastAPI 单进程)
                                  │
                                  ├── 智能体引擎（ReAct 循环 + 流式 SSE）
                                  ├── REST / SSE API
                                  └── 编译后的 Vue 静态前端（static/）
```

由 **FastAPI 单进程**统一托管三部分：① Agent 推理引擎 ② REST/SSE API ③ 编译后的 Vue 静态资源。前端通过同源调用后端，开发期与生产期共用同一套契约，彻底去掉 Node BFF。

---

## 三、核心模块

### 1. LLM 接入层（配置驱动多 Provider + 回退链）
- **抽象 `LLMProvider`**：基于 `httpx` 自研轻量客户端，零境外 SDK 依赖。
- **多 Provider 支持**：国产 OpenAI 兼容端点（通义千问 / 智谱 GLM / DeepSeek / Kimi / 文心一言 / MiniMax / 讯飞星火）+ 本地推理（Ollama / vLLM 经 OpenAI 兼容协议接入）。
- **故障回退链 + 多副本负载均衡**：配置的 Provider 按可用性与密钥状态自动筛选，主用不可用时回退至备用。
- **离线兜底**：未配置任何云端 Key 时，可指向本地 Ollama 实现离线问答。

### 2. 知识库与 RAG
- **向量库**：默认 `SQLite + numpy 余弦相似度`，零外部服务内网直用；可切换 `Milvus` / `PostgreSQL-pgvector` / `达梦(DM)`。
- **Embedding**：支持网络 Provider 端点（`embedding.embed_model`）或本地模型；未配置时降级为确定性哈希向量，保证检索流程不中断。
- **检索增强**：`rag.py` 提供知识库语义检索，结果注入对话上下文。

### 3. 记忆系统
- **会话记忆**：基于 SQLite FTS5 存储多轮对话，支持跨会话全文检索与回顾。
- **长期记忆工具**（`memory`）：Agent 可主动 `recall / save / update_user / forget`，持久化到 `MEMORY.md / USER.md / SOUL.md`，下一轮自动注入系统提示，实现跨会话连续性。
- **跨会话回忆工具**（`session_search`）：支持关键词检索、指定会话翻阅、最近会话浏览三种模式，零 LLM 成本。

### 4. 工具系统（Toolset）
工具按能力分组（`toolsets`），按需启用。内置工具包括：

| 工具分组 | 工具 | 说明 |
|----------|------|------|
| `terminal` | `terminal_run` | 沙箱内终端命令执行 |
| `file` | `file_read` / `file_write` / `file_list` | 文件读写（含大文件分页读取与零依赖文档解析） |
| `knowledge` | `knowledge_search` / `knowledge_list` / `knowledge_read` | 知识库检索 |
| `web` | `safe_web_fetch` / `web_search` | 受控外网访问（受 `outbound_allow` 门控）；`web_search` 支持多搜索引擎后端 |
| `memory` | `memory` | 长期记忆读写 |
| `todo` | `todo` | 任务清单（复杂任务拆解与进度跟踪） |
| `sessions` | `session_search` | 跨会话回忆 |
| `delegate` | `delegate_to_agent` | 委派子代理处理并行/专项任务 |
| `skills` | `read_skill` | 技能渐进披露（按需读取 SKILL.md 全文） |
| `code_exec` | `code_exec` | 沙箱内代码执行（自扩展能力） |

- **沙箱**：工具仅能在 `SANDBOX_ROOT`（默认 `data/sandbox`）范围内操作，防越权。
- **出网隔离**：`safe_web_fetch` 与 `web_search` 在执行前检查 `security.outbound_allow`，为 `False` 时拒绝出网。

### 5. 安全合规模块
- **数据脱敏**（`redact.py`）：对手机号 / 身份证 / 邮箱 / 银行卡 / 敏感字段值进行正则遮蔽，审计落库前自动脱敏。
- **国密与凭据保护**（`credentials.py` / `security.py`）：API Key 落盘前用 SM4 混淆（库缺失时降级）；鉴权 Token 基于 HMAC，可选 SM3 签名。
- **RBAC 权限**：角色与权限点（会话、知识库、模型、审计等）分级管控。
- **审计日志**：记录关键操作，落库前经脱敏处理。
- **私网隔离开关**：工具默认禁出网，仅白名单放行。

### 6. 上下文压缩
`context_engine.py` 在超长对话下自动截断 / 摘要 / 轮转，控制上下文窗口，保证长会话可用性。

### 7. 定时任务（Cron）
`cron.py` 提供内网合规版调度器，支持周期性触发对话或任务；配套 `api/cron.py` 路由与管理前端页面。

### 8. 技能自进化闭环
- **技能蒸馏**（`modules/skills.maybe_learn`）：复杂任务完成后，异步由 LLM 将可复用工作流沉淀为技能文件（`SKILL.md`）。
- **后台记忆反思**（`modules/skills.maybe_reflect`）：每轮结束后异步蒸馏用户事实追加进长期记忆（opt-in，默认关闭，异常安全）。
- **会话内 nudge**：每完成配置次数的工具调用，向模型注入内部提醒「该沉淀为长期记忆/技能了」，引导主动学习。

### 9. 多智能体协作
- **子代理委派**（`delegate_to_agent`）：将并行或专项任务交给子代理处理，主代理聚合结果。
- **MoA / 多模型协作**：支持多模型协作推理（见 `core/agent/moa.py`）。

---

## 四、目录结构

```
zhishu-agent/
├── README.md                 # 本文档
├── requirements.txt          # 依赖清单（国内 pip 源可用）
├── backend/
│   ├── start_backend.py      # 启动入口（uvicorn 托管 create_app，默认 0.0.0.0:8080）
│   └── zhishu/
│       ├── main.py           # FastAPI 应用工厂 create_app（引擎 + API + 静态托管）
│       ├── config.py         # YAML + 环境变量配置中心
│       ├── core/
│       │   ├── llm.py        # 多 Provider 适配（httpx，配置驱动）
│       │   ├── agent.py      # Agent 循环（ReAct + 流式 SSE）
│       │   ├── providers/    # Provider 客户端与回退链
│       │   ├── embedding.py  # Embedding（provider 网络 / 本地 / 哈希降级）
│       │   ├── vector_store.py # 向量库（sqlite / milvus / pgvector / dm 可配）
│       │   ├── rag.py        # 知识库检索增强
│       │   ├── memory/       # 会话记忆（SQLite FTS5）
│       │   ├── modules/      # 技能蒸馏 / 记忆反思等自进化模块
│       │   ├── security.py   # 鉴权 / RBAC / 审计
│       │   ├── redact.py     # 数据脱敏
│       │   ├── credentials.py# 凭据加密存储（SM4 混淆）
│       │   ├── cron.py       # 定时任务调度
│       │   ├── tools/        # 工具注册中心 + builtins/
│       │   └── agents_runtime.py # 子代理运行时
│       ├── api/              # chat / models / knowledge / auth / admin / cron / agents / users 路由
│       └── static/          # 编译后的前端资源（由 frontend 构建产出，已被 .gitignore 忽略）
├── frontend/                 # Vue3 + Vite + Naive UI（同源调用后端）
│   └── src/ ...
└── deploy/
    ├── zhishu.yaml.example   # 示例配置（复制为 zhishu.yaml 后填入密钥，真实配置不入库）
    ├── Dockerfile            # 标准容器化构建（node:18-alpine + python:3.11-slim）
    ├── Dockerfile.local      # 受限/离线网络专用（华为云 SWR 基础镜像 + 国内源，零代理）
    └── docker-compose.yml    # 一键编排（后端 + 可选本地 Ollama）
```

---

## 五、核心数据流

```
用户提问 → API(/api/v1/chat, SSE)
   → Agent 循环：
       1. 组装上下文：检索会话记忆(memory) + 知识库(rag) + 长期记忆(MEMORY.md)
       2. 调用 LLM（配置的 Provider / 本地模型，含回退链）
       3. 解析 tool_calls → 工具注册中心执行（沙箱 / 审计 / 出网门控）
       4. 结果回填，循环直至 final_answer
   → 流式返回 token / tool_call / done 事件
   → 异步触发：技能蒸馏(maybe_learn) 与（可选）记忆反思(maybe_reflect)
```

---

## 六、快速开始

### 6.1 部署前准备

```bash
# 1) 复制配置模板（deploy/zhishu.yaml 已被 .gitignore 忽略，请勿提交真实密钥）
cp deploy/zhishu.yaml.example deploy/zhishu.yaml
# 编辑 deploy/zhishu.yaml，填入你的 Provider api_key、管理员密码等

# 2) 安装后端依赖
cd backend && pip install -r ../requirements.txt -i https://mirrors.aliyun.com/pypi/simple
cd ..

# 3) （可选）自行构建前端：产物输出到 backend/zhishu/static，由 FastAPI 同源托管
cd frontend && npm install && npm run build && cd ..
```

> 仓库**不收录** `backend/zhishu/static/`（前端构建产物）与 `deploy/zhishu.yaml`（真实配置）。
> 若直接拉取源码运行，请先执行上面的「复制配置 + 构建前端」两步。

### 6.2 启动

```bash
cd backend && python start_backend.py
# 浏览器打开 http://127.0.0.1:8080
```

> **访问入口说明（重要）**：请直接打开 **FastAPI 后端地址（默认 http://127.0.0.1:8080）**，
> 后端会**同源托管前端页面与 `/api/v1/*` 接口**，登录等功能开箱即用。
> 不要通过 `npm run preview`（默认 4173）访问——那只是纯静态前端预览，**没有后端 API**，
> 调用登录会返回 HTTP 500。前端使用相对路径 `/api/v1/...`，由后端同源托管即可，无需任何代理。
> 默认账号 `admin` / `zhishu@2026`（生产请改 `deploy/zhishu.yaml` 中的 `admin_password`）。

---

## 七、关键配置项（deploy/zhishu.yaml）

| 配置段 | 关键字段 | 说明 |
|--------|----------|------|
| `llm.providers` | `name` / `type` / `base_url` / `api_key` / `models` | 多 Provider 接入，支持回退链 |
| `default_model` | — | 全局默认模型（严格跟随配置，无硬编码预设） |
| `embedding` | `backend` / `embed_model` / `fallback_hash` | provider 网络端点；未配置自动降级哈希向量 |
| `security` | `outbound_allow` / `secret` / `enable_sm` | 出网开关、国密密钥、SM 开关 |
| `web_search` | `backend` | 搜索引擎后端（bing_cn / duckduckgo / tavily / bing） |
| `agent` | `nudge_interval` / `reflection_enabled` / `skills_auto_learn` | 会话内提醒频率、后台记忆反思、技能自学习开关 |
| `cron` | — | 定时任务调度配置 |
| `memory` | — | 长期记忆存储路径与策略 |

完整字段见 `deploy/zhishu.yaml.example`。

---

## 八、Docker 容器化部署

镜像为「单进程」设计：一个容器同时跑 Agent 引擎 + REST/SSE API + 编译后的前端，对外只暴露 `8080`。

### 8.1 准备配置

```bash
cp deploy/zhishu.yaml.example deploy/zhishu.yaml
# 编辑 deploy/zhishu.yaml，填入 Provider api_key、管理员密码等
# 该文件已被 .gitignore 忽略，不会进入任何提交
```

### 8.2 构建镜像

提供两份 Dockerfile：

- `deploy/Dockerfile`：标准构建，基础镜像 `node:18-alpine` + `python:3.11-slim`，需要能访问 `docker.io`（或配置镜像加速）。
- `deploy/Dockerfile.local`：**受限 / 离线网络专用**，基础镜像走华为云 SWR（`ddn-k8s` 代理，直连可达，无需 `docker.io` / 代理），npm 走 npmmirror、pip 走腾讯云镜像。构建命令见文件头注释。

```bash
# 标准（需 docker.io 可达）
docker build -t zhishu-agent:1.0.0 -f deploy/Dockerfile .

# 受限网络 / 国内零代理（推荐内网、本机）
docker build -t zhishu-agent:1.0.0 -f deploy/Dockerfile.local .
```

> 构建会把 `deploy/zhishu.yaml` 打进镜像。若不想把密钥烧进镜像层，可先用占位配置（不填真实 Key）构建，运行时用只读卷挂载真实配置覆盖（见 8.4）。

### 8.3 运行（docker run）

```bash
docker run -d --name zsagent \
  -p 8080:8080 \
  -v zsagent_data:/app/backend/data \
  --restart unless-stopped \
  zhishu-agent:1.0.0
```

- `-v zsagent_data:/app/backend/data`：持久化向量库（`zhishu_vector.db`）、会话记忆与 `providers.json`，容器重建不丢数据。
- 浏览器打开 http://localhost:8080 ，默认账号 `admin` / `zhishu@2026`（生产请改 `deploy/zhishu.yaml` 的 `admin_password`）。

### 8.4 配置不烧进镜像（推荐生产）

先用占位配置构建（不填真实 Key），运行时只读挂载真实配置覆盖：

```bash
cp deploy/zhishu.yaml.example deploy/zhishu.yaml   # 占位，勿填真实 Key
docker build -t zhishu-agent:1.0.0 -f deploy/Dockerfile.local .
docker run -d --name zsagent -p 8080:8080 \
  -v "$(pwd)/deploy/zhishu.yaml:/app/deploy/zhishu.yaml:ro" \
  -v zsagent_data:/app/backend/data \
  --restart unless-stopped zhishu-agent:1.0.0
```

### 8.5 一键编排（docker compose）

```bash
# 先准备好 deploy/zhishu.yaml
docker compose -f deploy/docker-compose.yml up -d
```

compose 已声明 `zhishu-data` 数据卷与 `8080` 端口映射；可选启用内置的 Ollama 服务（取消注释 `ollama:` 段）做内网离线推理。

### 8.6 健康检查与排错

```bash
curl http://localhost:8080/health     # 期望 {"status":"ok",...}
docker logs -f zsagent                 # 查看启动 / 对话日志
```

- 若对话报「所有 LLM Provider 均不可用 / 429」：说明配置的 Provider 配额耗尽或不可达，需更换或补充 `providers.json` 中的可用 Key（在 `deploy/zhishu.yaml` 的 providers 段或运行时数据卷内调整）。
- 容器重启后配置与记忆均在 `zsagent_data` 卷中保留。

---

> 本系统为「**完整可运行骨架 + 核心模块真实实现**」，可直接二次开发接入具体模型与数据库。
