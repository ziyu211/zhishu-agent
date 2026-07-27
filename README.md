# 智枢智能体（Zhishu Agent）—— 国产化融合重构设计

> 一套代码，融合原 `hermes-agent`（Python 引擎）与 `hermes-web-ui`（Vue 前端 + Node BFF），
> 面向**内网离线、安全合规、国产自主可控**场景完全重写。

---

## 一、为什么要重构（国产替代动因）

| 维度 | 原架构（Hermes） | 国产痛点 |
|------|------------------|----------|
| LLM 客户端 | 强依赖 `openai`/`anthropic` 等**境外 SDK** | 境外 SDK 合规风险、出网依赖 |
| LLM 端点 | OpenAI / Anthropic / Google 等默认境外 | 内网无法访问 |
| 向量 / 记忆 | mem0、Pinecone、境外 embedding | 数据出网、离线不可用 |
| 前端链路 | Vue → Node(Koa) BFF → Python（**双服务**） | 镜像臃肿、部署复杂 |
| 安全合规 | 无国密、无审计、无 RBAC | 不满足政企内网要求 |
| 离线模型 | 无内置模型托管 | 必须自备云端 Key |

**结论**：两套装代码并非简单"合并"，而是以**国产自主可控**为第一原则重新设计——
去掉境外 SDK、去掉 Node BFF、内置离线模型与向量能力、补齐安全合规模块。

---

## 二、融合与国产化解耦策略

### 1. 部署结构融合（去 Node BFF）
原架构是「Python 引擎 + Node BFF + Vue」三段式。重构后：

```
浏览器 ──(同源 fetch / SSE)──▶ Python(FastAPI) ──▶ 静态前端 + 智枢引擎 + API
```

由 **FastAPI 单进程**同时托管：① Agent 引擎 ② REST/SSE API ③ 编译后的 Vue 静态资源。
前端通过同源调用，开发期与生产期使用同一套契约，彻底去掉 Node BFF，镜像体积减半、部署单元合一。

### 2. 模型层国产替代
- **抽象 `LLMProvider`**：内置国产 OpenAI 兼容端点——通义千问(DashScope)、智谱 GLM、DeepSeek、Kimi、文心一言、MiniMax、讯飞星火。
- **本地推理**：Ollama / vLLM / 国产推理框架（昇腾 CANN、百度 PaddleNLP、MindSpore）经 OpenAI 兼容协议接入。
- **自研轻量客户端**：基于 `httpx`，零境外 SDK；支持**故障回退链 + 多副本负载均衡**。
- **离线兜底**：未配置任何云端 Key 时，自动指向本地 Ollama，实现"开箱即离线问答"。

### 3. 知识库 / RAG 国产替代
- **内置本地向量库**：`SQLite + numpy 余弦相似度`，零外部服务，内网直接可用；可选接入 **Milvus（国产 Zilliz）** / **PostgreSQL-pgvector** / **达梦**。
- **离线 Embedding**：bge-zh / m3e / bge-large-zh，本地 `transformers` 或 Ollama 加载；无模型时降级为确定性哈希向量，保证流程不中断。

### 4. 数据库国产替代
默认 **SQLite**（轻量内网）；提供 **达梦(DM) / 人大金仓(Kingbase) / OceanBase / TiDB** 适配层（SQLAlchemy dialect）。

### 5. 安全合规模块（新增）
- **国密 SM2/SM3/SM4**（gmssl，缺失时降级至标准哈希，接口不变）。
- **审计日志**、**RBAC 权限**、**数据脱敏**、**私网隔离开关**（工具默认禁出网，白名单放行）。

### 6. 工具系统（保留并内网化）
terminal / filesystem / knowledge / safe_web，默认运行在**离线沙箱**（禁止出网，除非显式开启白名单）。

### 7. 离线部署模块（新增）
单容器镜像、国内镜像源（清华/阿里/腾讯云）、前端资源全打包无 CDN、YAML+环境变量配置中心、一键离线包（含模型与依赖）。

---

## 三、融合后目录结构

```
zhishu-agent/
├── README.md                 # 本文档
├── requirements.txt          # 纯国产/开源依赖，国内 pip 源
├── backend/
│   └── zhishu/
│       ├── main.py           # FastAPI 单进程：引擎 + API + 静态前端
│       ├── config.py         # YAML + 环境变量配置中心
│       ├── core/
│       │   ├── llm.py        # 国产 LLM Provider 适配（httpx，无境外SDK）
│       │   ├── agent.py      # Agent 循环（ReAct + 流式 SSE）
│       │   ├── tools.py      # 工具注册中心
│       │   ├── embedding.py  # 离线 Embedding（本地/ Ollama / 降级）
│       │   ├── vector_store.py # 本地向量库（SQLite + numpy）
│       │   ├── rag.py        # 知识库检索增强
│       │   ├── memory.py     # 会话记忆（SQLite FTS5）
│       │   └── security.py   # 国密 / RBAC / 审计
│       ├── tools/            # terminal / filesystem / knowledge / safe_web
│       ├── api/              # chat / models / knowledge / auth / admin 路由
│       └── static/          # 编译后的前端资源（由 frontend 构建产出，已被 .gitignore 忽略）
├── frontend/                 # Vue3 + Vite + Naive UI（同源调用后端）
│   └── src/ ...
└── deploy/
    ├── Dockerfile            # 国产基础镜像 + 国内源
    ├── docker-compose.yml
    ├── offline-build.sh      # 一键离线包
    └── zhishu.yaml.example   # 示例配置（复制为 zhishu.yaml 后填入密钥，真实配置不入库）
```

---

## 四、核心数据流

```
用户提问 → API(/api/v1/chat, SSE)
   → Agent 循环：
       1. 检索会话记忆(memory) + 知识库(rag) 组装上下文
       2. 调用 LLM(国产 Provider / 本地 Ollama，含回退链)
       3. 解析 tool_calls → 工具注册中心执行（沙箱/审计）
       4. 结果回填，循环直至 final_answer
   → 流式返回 token / tool_call / done 事件
```

---

## 五、如何运行

详见各目录 README 与 `deploy/`。

### 5.1 部署前准备

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

### 5.2 最简启动（需本地 Ollama 或配置国产 Key）

```bash
cd backend && python -m zhishu.main --config ../deploy/zhishu.yaml
# 浏览器打开 http://127.0.0.1:8080
```

> **访问入口说明（重要）**：请直接打开 **FastAPI 后端地址（默认 http://127.0.0.1:8080）**，
> 后端会**同源托管前端页面与 `/api/v1/*` 接口**，登录等功能开箱即用。
> 不要通过 `npm run preview`（默认 4173）访问——那只是纯静态前端预览，**没有后端 API**，
> 调用登录会返回 HTTP 500。前端用相对路径 `/api/v1/...`，因此只要由后端同源托管即可，无需任何代理。
> 默认账号 `admin` / `zhishu@2026`（生产请改 `deploy/zhishu.yaml` 中的 `admin_password`）。

完整代码见后续文件。本设计为"**完整可运行骨架 + 核心模块真实实现**"，可直接二次开发接入贵司具体模型与数据库。
