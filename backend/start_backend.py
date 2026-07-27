"""智枢后端启动脚本（脱离工作目录依赖，便于后台/守护启动）。"""
import os
import sys

# 无论从哪里调用，都切换到本脚本所在目录（即 backend/）
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

from zhishu.core.config import ZhishuConfig
from zhishu.main import create_app
import uvicorn

# 配置优先读取仓库根 deploy/zhishu.yaml（与前端同目录），其次回退 backend/deploy/zhishu.yaml
_ROOT_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", "zhishu.yaml")
_LOCAL_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy", "zhishu.yaml")
cfg = ZhishuConfig.load(_ROOT_YAML if os.path.exists(_ROOT_YAML) else _LOCAL_YAML)
app = create_app(cfg)

print("智枢 backend 就绪，监听 0.0.0.0:8080", flush=True)
uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
