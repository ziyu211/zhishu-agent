#!/usr/bin/env bash
# 智枢智能体 —— 内网离线打包脚本
# 用途：在可联网的构建机上打包，拷贝到内网离线机直接运行，无需访问任何境外源。
#
# 生成产物：zhishu-offline-<date>.tar.gz
#   包含：backend/（Python 源码 + 依赖 wheels）、frontend 已构建静态资源、
#         deploy/（配置 + 镜像构建文件）、requirements、离线 README。
#
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
DATE=$(date +%Y%m%d)
OUT="zhishu-offline-$DATE"
mkdir -p "$OUT"

echo "[1/5] 收集源码与配置"
mkdir -p "$OUT/backend" "$OUT/frontend" "$OUT/deploy"
cp -r backend/zhishu "$OUT/backend/"
cp requirements.txt "$OUT/backend/"
cp -r deploy/* "$OUT/deploy/"

echo "[2/5] 构建前端（国内 npm 源）"
cd "$ROOT/frontend"
npm config set registry https://registry.npmmirror.com
npm install
npm run build   # 产物输出到 ../backend/zhishu/static
cd "$ROOT"
cp -r backend/zhishu/static "$OUT/backend/zhishu/"

echo "[3/5] 下载 Python 依赖离线 wheels（国内 pip 源）"
mkdir -p "$OUT/wheels"
pip download -r requirements.txt \
  -i https://mirrors.aliyun.com/pypi/simple \
  -d "$OUT/wheels"
# 可选：把本地模型（如 Ollama 的 qwen2.5:7b）导出一并打包
# cp -r ~/.ollama "$OUT/ollama-models"

echo "[4/5] 生成离线安装脚本"
cat > "$OUT/install-offline.sh" <<'EOF'
#!/usr/bin/env bash
# 内网离线安装：无需任何外网访问
set -e
# 进入 backend/ 目录，使 zhishu 包可经 `python -m zhishu.main` 导入；
# 相对路径统一相对 backend/ 解析（wheels 在 ../wheels，配置在 ../deploy）。
cd "$(dirname "$0")/backend"
python3 -m venv venv
source venv/bin/activate
pip install --no-index --find-links ../wheels -r requirements.txt
echo "安装完成。启动："
echo "  python -m zhishu.main --config ../deploy/zhishu.yaml --static zhishu/static"
EOF
chmod +x "$OUT/install-offline.sh"

echo "[5/5] 打包"
tar czf "$OUT.tar.gz" "$OUT"
echo "完成：$OUT.tar.gz"
echo "内网机解压后执行 ./install-offline.sh 即可运行。"
