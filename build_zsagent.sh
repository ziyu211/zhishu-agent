#!/usr/bin/env bash
# 构建 zsagent 镜像（零代理依赖版）
# 基础镜像走华为云 SWR ddn-k8s（直连可达，不经 docker.io/代理）
# npm/pip 走国内源（npmmirror / 腾讯云，均直连可达）
set -e
# 远程部署目录（脚本在远程 Linux 主机执行；本地 Windows 调试请用绝对路径）
cd /opt/zhishu-agent
echo "build start: $(date)"
docker build -t "zsagent:${ZSAGENT_VERSION:-1.0.62}" -f deploy/Dockerfile.local . 2>&1
echo "BUILD_EXIT=$?"
