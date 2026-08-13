# 智枢智能体 —— 单容器镜像（国产基础镜像 + 国内源，适配受限/离线网络）
# 构建：在 zhishu-agent/ 目录下执行  docker build -t zhishu-agent:1.0.30 -f deploy/Dockerfile .
# 说明：去掉了原架构的 Node BFF，前端构建产物直接由 Python 托管。
# 基础镜像走华为云 SWR ddn-k8s（直连可达，不经 docker.io/代理）；配置用 example（不含密钥，运行时应挂载真实 zhishu.yaml）。

# ---------- 阶段1：构建前端（Vue3 + Vite + Naive UI）----------
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/node:18-alpine AS fe
WORKDIR /app
# 使用国内 npm 镜像
RUN npm config set registry https://registry.npmmirror.com
COPY frontend/ ./frontend/
RUN cd frontend && npm install && npm run build   # 产物输出到 /app/backend/zhishu/static

# ---------- 阶段2：Python 运行环境 ----------
# 国产替代提示：可替换为 华为云/阿里云/腾讯云 的 python 基础镜像，
# 例如 FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim AS be
WORKDIR /app

# 使用国内 pip 镜像（离线环境可改为私有源或离线 wheels 目录）
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple

# 安装 Python 依赖（requirements.txt 在项目根，需一并复制进 backend/）
COPY backend/ ./backend/
COPY requirements.txt ./backend/requirements.txt
COPY --from=fe /app/backend/zhishu/static ./backend/zhishu/static
RUN pip install --no-cache-dir -r backend/requirements.txt

# 拷贝配置（用 example，不含密钥；生产运行请挂载真实 zhishu.yaml 覆盖）
COPY deploy/zhishu.yaml.example ./deploy/zhishu.yaml

EXPOSE 8080
# 以 backend 为工作目录运行，-m zhishu.main 会自动托管 static/ 前端
WORKDIR /app/backend
CMD ["python", "-m", "zhishu.main", "--config", "/app/deploy/zhishu.yaml"]
