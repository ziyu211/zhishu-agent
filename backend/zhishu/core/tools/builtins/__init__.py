"""智枢智能体 —— 内置工具实现集合（对标 Hermes `tools/*.py` 每工具一文件）。

  导入本包即触发各工具模块的 @tool 自注册。受限工作区根目录在此统一建立。
"""
from __future__ import annotations

# 受限工作区根目录（工具只能在此范围内写文件，防止越权读内网其它文件）。
# 该目录仅作内部执行 cwd 使用，绝不暴露给模型，模型拿到的永远是 /media/... 下载链接。
# 实际定义与按 owner 隔离的子目录逻辑统一收敛到 sandbox 子模块。
from .sandbox import SANDBOX_ROOT, sandbox_cwd_for  # noqa: E402,F401

from . import terminal, file, web, knowledge, delegate, skills, code_exec, sessions, todo, memory, agent_admin, compare, generate_excel  # noqa: E402,F401
