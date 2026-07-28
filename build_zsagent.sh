#!/usr/bin/env bash
# 构建 zsagent 镜像（零代理依赖版）
# 基础镜像走华为云 SWR ddn-k8s（直连可达，不经 docker.io/代理）
# npm/pip 走国内源（npmmirror / 腾讯云，均直连可达）
set -e
cd /d/data/hemers/zhishu-agent
echo "build start: $(date)"
docker build -t zsagent:1.0.0 -f deploy/Dockerfile.local . 2>&1
echo "BUILD_EXIT=$?"
