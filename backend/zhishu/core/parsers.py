"""智枢智能体 —— 上传解析的「按需插件」编目与调度。

设计目标（对应需求：文档/图片上传到对话框后解析；若需要插件则询问用户、
用户确认后直接安装）：

  * PARSE_PLUGINS：内置解析插件编目。每个条目描述它能解析哪些扩展名、
    对应的 helper 脚本（自包含、可自动 pip 安装依赖），以及注册到 Agent 的
    工具规格。安装时把脚本复制到 data/plugins/<name>/ 下。
  * 后端 /api/v1/chat/attach 先尝试内置解析；当内置能力缺失（缺库）时，
    通过 get_plugin_for_ext / needs_plugin_for_error 反查出所需插件名，
    返回 needs_plugin 给前端；前端据此询问用户是否安装。
  * install_plugin()：把编目项落地为真正的插件模块（写 module.json + 复制脚本 +
    注册工具），实现「用户确认后直接安装」。
  * run_plugin_parse()：已安装插件后，直接调用其 helper 脚本完成解析。

注：本系统已内置 OCR（tesseract + 中文包），图片/扫描 PDF 文字可由 read_file 经 OCR 提取；
纯图片若无文字则作为视觉参考进入对话。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from ..context import get_ctx
from .modules import module_dir, read_meta, write_meta, sanitize_name, DISABLED_KEY


# 编目脚本所在目录（与 parsers.py 同级的 parsers_scripts/）
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "parsers_scripts")


def _script_abs(name: str) -> str:
    """安装后插件脚本的绝对路径（data/plugins/<name>/<script>）。"""
    return os.path.join(module_dir("plugins", name), name + "_script.py")


def _build_tool(name: str, script: str, description: str) -> dict:
    """构造注册到 Agent 的插件工具规格（shell 类，调用 helper 脚本）。"""
    return {
        "name": f"parse_{name.split('-')[-1]}",
        "description": description,
        "type": "shell",
        "command": sys.executable,
        "args": [_script_abs(name), "{{path}}"],
        "command_is_template": False,
        "parameters": [
            {"name": "path", "type": "string", "description": "待解析文件的本地路径", "required": True}
        ],
    }


# ── 解析插件编目 ───────────────────────────────────────────────
PARSE_PLUGINS: dict[str, dict] = {
    "parser-docx": {
        "name": "parser-docx",
        "description": "【已废弃】Word 文档(.docx)解析已由 read_file 工具统一接管，请勿再调用；若仍被调用，内部会自动委托标准库提取。",
        "version": "2.0.0",
        "enabled": True,
        "exts": [".docx", ".doc"],
        "script": "parser-docx",
        "pip": [],
    },
    "parser-xlsx": {
        "name": "parser-xlsx",
        "description": "【已废弃】Excel 表格(.xlsx/.xls)解析已由 read_file 工具统一接管，请勿再调用；若仍被调用，内部会自动委托标准库提取。",
        "version": "2.0.0",
        "enabled": True,
        "exts": [".xlsx", ".xls"],
        "script": "parser-xlsx",
        "pip": [],
    },
    "parser-pdf": {
        "name": "parser-pdf",
        "description": "【已废弃】PDF 文本提取已由 read_file 工具统一接管，请勿再调用；若仍被调用，内部会自动委托标准库提取。",
        "version": "2.0.0",
        "enabled": True,
        "exts": [".pdf"],
        "script": "parser-pdf",
        "pip": ["pypdf", "pdfminer.six"],
    },
}


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def get_plugin_for_ext(filename: str) -> str | None:
    """根据扩展名返回所需解析插件名（无则返回 None）。"""
    ext = _ext(filename)
    for name, info in PARSE_PLUGINS.items():
        if ext in info.get("exts", []):
            return name
    return None


def needs_plugin_for_error(filename: str, err: Exception) -> str | None:
    """内置解析抛错时，尝试反查所需插件（依据扩展名或错误信息中的库名）。"""
    name = get_plugin_for_ext(filename)
    if name:
        return name
    msg = str(err).lower()
    if "python-docx" in msg or "docx" in msg:
        return "parser-docx"
    if "openpyxl" in msg or "excel" in msg:
        return "parser-xlsx"
    if "pypdf" in msg or "pdfminer" in msg or "pdf" in msg:
        return "parser-pdf"
    return None


def is_plugin_installed(name: str) -> bool:
    d = module_dir("plugins", name)
    if not os.path.isdir(d):
        return False
    state = __import__("json").load(open(
        os.path.join(get_ctx().cfg.server.data_dir, "modules_state.json"), encoding="utf-8"
    )) if os.path.isfile(os.path.join(get_ctx().cfg.server.data_dir, "modules_state.json")) else {}
    disabled = set(state.get(DISABLED_KEY["plugins"], []))
    return name not in disabled


def run_plugin_parse(name: str, filepath: str) -> str:
    """调用已安装插件的 helper 脚本解析文件，返回纯文本。失败抛 RuntimeError。"""
    script = _script_abs(name)
    if not os.path.isfile(script):
        raise RuntimeError(f"插件脚本缺失：{script}（请重新安装插件 {name}）")
    proc = subprocess.run(
        [sys.executable, script, filepath],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not out:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip() or "（无输出）"
        raise RuntimeError(f"{name} 解析未完成：{err}")
    return out


def install_plugin(name: str, descriptor: dict | None = None) -> dict:
    """把编目中的解析插件落地为可运行插件（写 meta + 复制脚本 + 注册工具）。"""
    name = sanitize_name(name)
    if not name:
        raise ValueError("插件名称非法")
    info = PARSE_PLUGINS.get(name)
    if info is None and not descriptor:
        raise ValueError(f"未知插件：{name}（仅支持内置解析插件或提供完整 descriptor）")

    # 组装插件元信息
    meta = {
        "name": name,
        "description": (descriptor or {}).get("description") or info["description"],
        "version": (descriptor or {}).get("version") or info.get("version", "1.0.0"),
        "enabled": True,
        "tools": [],
    }
    if descriptor and descriptor.get("tools"):
        meta["tools"] = descriptor["tools"]
    elif info:
        meta["tools"] = [
            _build_tool(name, info["script"], info["description"])
        ]

    # 写 meta（module.json）
    write_meta("plugins", name, meta)

    # 复制 helper 脚本
    if info:
        src = os.path.join(_SCRIPTS_DIR, info["script"] + ".py")
        if os.path.isfile(src):
            shutil.copyfile(src, _script_abs(name))

    # 注册工具到 Agent（使其立即可用）
    from .modules.plugins import register_plugin_tools

    register_plugin_tools()
    return {
        "ok": True,
        "name": name,
        "tool_count": len(meta["tools"]),
        "installed": True,
    }
