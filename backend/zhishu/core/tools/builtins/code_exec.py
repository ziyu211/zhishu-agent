"""自扩展代码执行（对标 Hermes「读取失败即由 LLM 生成 Python 自创工具」能力）。

提供两个工具：
  * code_exec   —— 在受控子进程中运行模型生成的 Python（一次性），用于临时的、
                  标准工具（read_file / 解析器）处理不了的文件或任务。
  * create_tool —— 把一段 Python 注册为可复用的动态工具（进程级，会话内可反复调用）。

护栏（默认开启，受 security 配置控制）：
  * 子进程 cwd 锁在沙箱根，避免越权写盘；
  * 超时（security.code_exec_timeout，上限 120s）后强杀；
  * 内存上限（security.code_exec_mem_limit_mb，posix 下 via RLIMIT_AS）；
  * 默认禁用网络（覆盖 socket.socket，覆盖 urllib/requests/httpx 等常见的出网路径）；
  * 输出截断到 8000 字符（注册中心再次截断，双保险）；
  * 总开关 security.allow_code_exec=false 时整体拦截。

说明：子进程内执行的是模型自身生成的代码，属「受控自修改」能力；其安全边界依赖
内网可信部署 + 上述护栏。如要更硬隔离，应在基础设施层（容器 / 防火墙）再加固。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from typing import Optional

from ..base import tool, Tool, ToolContext
from .. import registry as _registry
from .artifacts import snapshot, publish_diff, publish_referenced_paths, append_unique_links
from .sandbox import sandbox_cwd_for, SANDBOX_ROOT

# 全局动态工具登记（进程级）。键为工具名，值为 (描述, 代码, 参数schema, 会话)
CREATED_TOOLS: dict[str, tuple[str, str, dict, str]] = {}

MAX_OUTPUT = 8000          # 单段输出上限
MAX_TIMEOUT = 120          # 超时硬上限（秒）
_DYN_PREFIX = "dyn_"       # 动态工具名前缀，避免与内置冲突
_MAX_DYNAMIC = 64          # 动态工具数量上限，防止工具列表无限膨胀

# 网络禁用引导：在 socket 模块层把「真正建立连接」的入口（socket.__init__ /
# create_connection / getaddrinfo / create_server）替换为抛错，但**保留 socket 为类**，
# 以免 ssl.py 等模块在 import 期做 `class SSLSocket(socket)` 时崩溃。这是「提高门槛」的
# 软护栏，并非绝对不可绕过；硬隔离交给基础设施层（容器 / 防火墙）。
_NETWORK_BLOCK = '''
import socket as _zh_socket
_orig_socket_init = _zh_socket.socket.__init__
def _zh_socket_init(self, *a, **k):
    raise RuntimeError("code_exec 已禁用网络访问（内网隔离）")
_zh_socket.socket.__init__ = _zh_socket_init
def _zh_blocked(*a, **k):
    raise RuntimeError("code_exec 已禁用网络访问（内网隔离）")
_zh_socket.create_connection = _zh_blocked
_zh_socket.getaddrinfo = _zh_blocked
_zh_socket.create_server = _zh_blocked
'''


def _code_exec_allowed(ctx: Optional[ToolContext]) -> bool:
    """代码执行授权：user / operator / admin，且 security.allow_code_exec 开启。
    fail-closed：无安全配置或非授权角色一律拒绝；viewer 仍为只读角色不开放代码执行。"""
    role = getattr(ctx, "user_role", None)
    if role not in ("admin", "operator", "user"):
        return False
    sec = getattr(ctx, "security", None)
    if sec is None:
        return False
    return bool(getattr(sec, "allow_code_exec", False))


def _block_network(ctx: Optional[ToolContext]) -> bool:
    """code_exec 子进程是否禁网：由独立开关 security.code_exec_network_isolated 控制，
    默认 False（不隔离、允许出网），**与全局 outbound_allow 解耦**。

    设计取舍：code_exec 是模型自生成代码的「受控自修改」能力，用户要求其在内网/抓数据
    等场景下不受全局 outbound_allow 影响仍可执行与出网。因此这里不再读 outbound_allow，
    而是用 code_exec 专属开关；真正的网络硬隔离仍由基础设施层（防火墙 / 容器 egress 策略）兜底。
    仅当某部署需在应用层再拦 code_exec 出网时，将 code_exec_network_isolated 置 True。
    """
    sec = getattr(ctx, "security", None)
    if sec is None:
        return False
    return bool(getattr(sec, "code_exec_network_isolated", False))


def _resolve_path(path: str, owner: Optional[str] = None,
                 is_admin: bool = False) -> Optional[str]:
    """复用 file 工具的路径解析（越权校验 + 绝对化），按用户收窄媒体目录。"""
    from .file import _resolve_read_path
    try:
        return _resolve_read_path(path, owner, is_admin)
    except Exception:
        return None


def _preexec(mem_limit_mb: int):
    """posix 下限制子进程地址空间（虚拟内存）。"""
    if not mem_limit_mb or os.name != "posix":
        return
    try:
        import resource as _r
        lim = mem_limit_mb * 1024 * 1024
        _r.setrlimit(_r.RLIMIT_AS, (lim, lim))
    except Exception:
        pass


async def _run_python(code: str, timeout: int, extra_env: dict, cwd: str,
                      mem_limit_mb: int = 0, block_network: bool = True) -> str:
    timeout = max(1, min(int(timeout or 30), MAX_TIMEOUT))
    full = (_NETWORK_BLOCK if block_network else "") + "\n" + code
    fd, path = tempfile.mkstemp(suffix=".py", prefix="zh_code_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(full)
        env = dict(os.environ)
        env.update(extra_env or {})
        env["PYTHONUNBUFFERED"] = "1"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd, env=env,
            preexec_fn=_preexec(mem_limit_mb) if os.name == "posix" else None,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"[code_exec 超时] 超过 {timeout}s 已强制终止"
        text = (out or b"").decode("utf-8", "ignore")
        # 兼容 Windows GBK 输出（终端打印中文时常见）
        if not text.strip() and out:
            try:
                text = out.decode("gbk", errors="ignore")
            except Exception:
                pass
        # 子进程未捕获异常会以非零退出码结束并打印 traceback。若其中含有我们已知护栏
        # 子进程未捕获异常会以非零退出码结束并打印 traceback。若其中含有我们已知护栏
        # 抛出的 RuntimeError（如「已禁用网络」），提炼为干净的拦截提示，避免把
        # traceback 原样回显给模型。
        if proc.returncode not in (0, None) and "已禁用网络" in text:
            text = "[code_exec] 已禁用网络访问（内网隔离），代码未执行出网操作。"
        return text[:MAX_OUTPUT].strip()
    except Exception as e:  # noqa: BLE001
        return f"[code_exec 错误] {e}"
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def _collect_outputs(out_dir: str, media, owner: str) -> list[tuple[str, str]]:
    """收集临时输出目录内的新文件，逐个落盘到媒体库并返回 (文件名, /media/... URL)。"""
    results: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                with open(fp, "rb") as f:
                    data = f.read()
            except Exception:
                continue
            if not data:
                continue
            ext = os.path.splitext(fn)[1].lstrip(".") or "bin"
            try:
                url = media.save_bytes(data, kind="file", ext=ext, owner=owner)
                results.append((fn, url))
            except Exception:
                continue
    return results


def _make_handler(code: str, mem_limit_mb: int, timeout: int, block_network: bool):
    async def handler(args: dict, ctx: ToolContext) -> str:
        if not _code_exec_allowed(ctx):
            return "[已拦截] 当前配置禁止代码执行（security.allow_code_exec=false）"
        extra_env = {
            "TOOL_ARGS_JSON": json.dumps(args or {}, ensure_ascii=False),
            "TOOL_SESSION": getattr(ctx, "session", "default"),
        }
        cwd = sandbox_cwd_for(getattr(ctx, "user", "anonymous") or "anonymous")
        return await _run_python(
            code, timeout, extra_env, cwd,
            mem_limit_mb=mem_limit_mb, block_network=block_network,
        )
    return handler


def _register_dynamic(tname: str, desc: str, code: str, params: dict,
                      session: str, mem_limit_mb: int, timeout: int, block_network: bool):
    if len(CREATED_TOOLS) >= _MAX_DYNAMIC:
        # 超出上限时移除最早一条，保证始终可注册
        old = next(iter(CREATED_TOOLS))
        CREATED_TOOLS.pop(old, None)
        _registry.ToolRegistry.unregister(old)
    _registry.ToolRegistry.register(Tool(
        name=tname,
        description=desc,
        parameters=params,
        handler=_make_handler(code, mem_limit_mb, timeout, block_network),
        toolset="dynamic",
    ))
    CREATED_TOOLS[tname] = (desc, code, params, session)


@tool(
    "code_exec",
    "【提速关键】优先把『整个任务』写成一个完整 Python 脚本，用 code 参数一次性 code_exec 跑完"
    "（读取文件/处理/生成产物全在一个脚本里），能少跑多次 LLM 往返、明显更快；"
    "不要把一个任务拆成多次零散的 code_exec 调用（如连跑 8 次），那正是「智枢比 Hermes 慢」的主因；"
    "严禁 REPL 式逐步调试（写一段跑一段、看输出再改下一段），请一次性规划完整脚本。"
    "需要分段时也可用 snippets 列表（例：snippets:[\"import pandas as pd\",\"df=pd.read_csv(TARGET_FILE)\",\"print(df.describe())\"]），"
    "系统在单个子进程内顺序执行、共享变量（与 code 等价，可选）。"
    "运行 Python 代码（对标 Hermes 自创工具/自愈能力）。当 read_file 或解析器遇到不支持的文件格式、"
    "或需要标准工具没有的处理逻辑时，可编写 Python 打印结果来解决。可选 path 参数会把文件绝对路径注入 "
    "TARGET_FILE 环境变量，代码内用 os.environ['TARGET_FILE'] 读取。代码须把结果 print 到 stdout。"
    "代码产生的新文件会【自动】落盘到媒体库并回传 /media/... 下载链接，无需额外参数；"
    "save_output=true 时额外收集 ZHISHU_OUTPUT_DIR 目录内的文件再发布一次。"
    "若要把结果导出为 Excel：最稳妥的做法是在代码里把表格写成 CSV 文件"
    "（例如 open('result.csv','w',encoding='utf-8').write(csv_text)），该文件会自动发布并返回 /media 链接；"
    "随后调用 generate_excel(filename='结果.xlsx', from_file='<刚才的 /media 链接或沙箱文件名>') 即可生成合法 xlsx。"
    "也可在代码里直接用 openpyxl 写 .xlsx（同样会自动发布）。"
    "注意：切勿把表格只 print 出来却不落盘——那样 generate_excel 拿不到数据、只会报错。"
    "注意：这是模型自生成的代码，仅在内网可信部署下使用。",
    {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码（结果请 print 到 stdout）；与 snippets 二选一"},
            "snippets": {"type": "array", "description": "批量代码段：多个 Python 代码片段列表，合并为一段在单个子进程内顺序执行（共享解释器与导入，变量跨段保留，消除多次冷启动、减少往返）", "items": {"type": "string"}},
            "path": {"type": "string", "description": "可选：目标文件 stored_path / /media/ URL，将作为 TARGET_FILE 环境变量供代码读取"},
            "timeout": {"type": "integer", "description": "超时秒数，默认取 security.code_exec_timeout，上限 120"},
            "save_output": {"type": "boolean", "description": "为 true 时收集代码生成的文件并落盘到媒体库，回传 /media/... 可下载链接"},
        },
        "required": [],
    },
    toolset="core",
)
async def code_exec(args: dict, ctx: ToolContext) -> str:
    if not _code_exec_allowed(ctx):
        return "[已拦截] 当前配置禁止代码执行（security.allow_code_exec=false）"
    snippets = args.get("snippets") or []
    if isinstance(snippets, str):
        snippets = [snippets]
    code = (args.get("code") or "").strip()
    if not code and snippets:
        # 合并多段代码在单个子进程内顺序执行（变量跨段保留），消除多次冷启动
        code = "\n\n".join(s.strip() for s in snippets if isinstance(s, str) and s.strip())
    _used_singular = bool(code) and not snippets
    if not code:
        return "[code_exec] 缺少 code / snippets 参数"
    sec = getattr(ctx, "security", None)
    default_to = getattr(sec, "code_exec_timeout", 30) or 30
    mem = getattr(sec, "code_exec_mem_limit_mb", 0) or 0
    extra_env: dict = {}
    path = args.get("path")
    if path:
        rp = _resolve_path(path, getattr(ctx, "user", None), getattr(ctx, "is_admin", False))
        if not rp:
            return f"[code_exec] 路径越权或不存在: {path}"
        extra_env["TARGET_FILE"] = rp
    media = getattr(ctx, "media", None)
    owner = getattr(ctx, "user", "anonymous") or "anonymous"
    cwd = sandbox_cwd_for(owner)
    # save_output=true 时额外收集 ZHISHU_OUTPUT_DIR（工作区的新文件始终自动发布）
    explicit_save = bool(args.get("save_output"))
    out_dir = None
    if explicit_save and media is not None:
        out_dir = tempfile.mkdtemp(prefix="zh_out_")
        extra_env["ZHISHU_OUTPUT_DIR"] = out_dir
    # 执行前快照工作区，用于差分出本次新增/修改的文件
    before = snapshot(cwd) if media is not None else {}
    result = await _run_python(
        code, args.get("timeout", default_to), extra_env, cwd,
        mem_limit_mb=mem, block_network=_block_network(ctx),
    )
    if media is not None:
        # 自动发布工作区内本次新增/修改的文件（核心修复：不再依赖模型传参）
        result += publish_diff(cwd, before, media, owner)
        if out_dir:
            files = _collect_outputs(out_dir, media, owner)
            shutil.rmtree(out_dir, ignore_errors=True)
            if files:
                result += "\n\n[输出目录生成的可下载文件]:\n" + "\n".join(
                    f"- [{n}]({u})" for n, u in files)
        # 兜底：捕获模型写到 cwd/output 之外、却把内部绝对路径回显给用户的真实产物
        # （如 data/generated/attachments/<owner>/...、/tmp、挂载卷等），统一补出 /media 链接。
        _ref_text, _refs = publish_referenced_paths(
            result, media, owner,
            media_root=getattr(media, "root", None),
            sandbox_root=SANDBOX_ROOT, out_dir=out_dir,
        )
        if _refs:
            result = append_unique_links(_ref_text, _refs)
    if _used_singular:
        result += ("\n\n[提速提示] 本次只跑了 1 段 Python。若还需运行多段脚本（如先 import 再多次处理），"
                   "请用 snippets 列表一次提交，系统在单个子进程内顺序执行、共享变量，避免每段都冷启动。")
    return result


@tool(
    "create_tool",
    "把一段 Python 注册为可复用的动态工具（对标 Hermes 自创持久工具）。"
    "注册后可直接以工具名调用，适合对某种文件/任务的稳定处理逻辑反复使用。"
    "代码须把结果 print 到 stdout；调用时传入的参数会以 JSON 字符串注入环境变量 "
    "TOOL_ARGS_JSON，代码中用 json.loads(os.environ['TOOL_ARGS_JSON']) 读取。"
    "工具名将自动加 'dyn_' 前缀。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "工具名（字母/数字/下划线，2-31 字符，纯字母或下划线开头），将加 dyn_ 前缀"},
            "description": {"type": "string", "description": "工具用途描述，帮助模型决定是否调用"},
            "code": {"type": "string", "description": "工具体 Python 代码（结果请 print 到 stdout）"},
            "parameters": {"type": "object", "description": "可选：工具的 JSON Schema 参数声明，缺省为无参数 {}"},
        },
        "required": ["name", "code"],
    },
    toolset="core",
)
async def create_tool(args: dict, ctx: ToolContext) -> str:
    if not _code_exec_allowed(ctx):
        return "[已拦截] 当前配置禁止代码执行（security.allow_code_exec=false）"
    name = (args.get("name") or "").strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{1,30}$", name):
        return "[create_tool] 工具名需为字母/数字/下划线，2-31 字符，且以字母或下划线开头"
    code = (args.get("code") or "").strip()
    if not code:
        return "[create_tool] 缺少 code 参数"
    tname = _DYN_PREFIX + name
    desc = args.get("description") or f"用户自定义工具 {name}（由 code_exec 引擎运行）"
    params = args.get("parameters") or {"type": "object", "properties": {}, "required": []}
    sec = getattr(ctx, "security", None)
    mem = getattr(sec, "code_exec_mem_limit_mb", 0) or 0
    to = getattr(sec, "code_exec_timeout", 30) or 30
    _register_dynamic(tname, desc, code, params, getattr(ctx, "session", "default"), mem, to, _block_network(ctx))
    return f"[create_tool] 已注册工具 '{tname}'，后续步骤可直接调用；参数将通过 TOOL_ARGS_JSON 传入。"
