"""智枢智能体 —— 内置工具实现集合（对标 Hermes `tools/*.py` 每工具一文件）。

  导入本包即触发各工具模块的 @tool 自注册。沙箱根目录在此统一建立。
"""
from __future__ import annotations

import os

# 沙箱根目录（工具只能在此范围内操作，防止越权读内网其它文件）
SANDBOX_ROOT = os.environ.get("ZHISHU_SANDBOX", "data/sandbox")
os.makedirs(SANDBOX_ROOT, exist_ok=True)

from . import terminal, file, web, knowledge, delegate, skills, code_exec, sessions, todo, memory  # noqa: E402,F401
