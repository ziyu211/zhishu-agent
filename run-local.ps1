# 本地 zsagent 容器启动脚本（源码挂卷 + 自动同步）
# 用途：本地改 backend/zhishu 下任意代码后，只需 `docker restart zsagent` 即生效，无需 docker cp。
# 注意：必须带 ZHISHU_ALLOW_INSECURE_DEFAULTS=1，否则镜像默认密钥会触发 main.py 安全闸门导致崩溃循环。
# 数据卷 zsagent_data 持久化用户/Provider/对话等，不会因重建容器丢失。

$ErrorActionPreference = "Stop"

# 若已存在同名容器先删除（数据在卷里，安全）
docker rm -f zsagent 2>$null

docker run -d `
  --name zsagent `
  --restart unless-stopped `
  -p 8080:8080 `
  -e ZHISHU_ALLOW_INSECURE_DEFAULTS=1 `
  -v zsagent_data:/app/backend/data `
  -v "D:\data\hemers\zhishu-agent\backend\zhishu:/app/backend/zhishu" `
  zsagent:1.0.18

Start-Sleep -Seconds 8
docker ps --filter name=zsagent --format "{{.Names}} {{.Status}}"
docker logs zsagent --tail 15
