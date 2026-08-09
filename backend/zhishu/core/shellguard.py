"""智枢智能体 —— Shell 命令安全闸门（cron shell 任务 / terminal_run 工具共用）。

背景（本文件修复的断点）：
  * `cron._run_shell` 直接 `create_subprocess_shell(job["payload"])`，除了把 cwd
    指到 data/sandbox 之外**没有任何护栏**：命令内容不过滤、继承宿主全量环境变量
    （含 ZHISHU_SECRET / 各 Provider Key）、超时后只 kill 直接子进程（shell 派生的
    孙进程继续留守）、无资源上限。一个 operator 建一条定时任务即可读走密钥或打穿宿主机。
  * `terminal_run` 工具此前只有角色门（user+）没有命令门。

本模块提供三件事，两处调用点复用同一套策略：
  1. `check_command()` —— 拒绝清单（毁灭性/提权/外传，对完整命令文本检查）+ 可执行
     文件白名单（引号感知切段 + 剥离 heredoc 体后，逐段校验首个 token），并禁止命令替换
     以防绕过白名单。引号内的 ; & | \n 与 heredoc 正文不再被误判为命令分隔符。
  2. `sandbox_env()`   —— 最小化环境变量，剔除一切密钥类变量，杜绝 `env` 外传。
  3. `run_guarded()`   —— 独立进程组 + 超时整组击杀 + POSIX rlimit（CPU/内存/文件
     大小/进程数）+ 输出截断的统一执行入口。

设计取舍：这是**内网可信部署**下的纵深防御，不是容器级隔离。真正的强隔离应由
部署方把整个智枢跑在容器 / 受限用户下；本模块负责把"一条定时任务就能提权"这种
低门槛路径堵死。
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
from typing import Iterable, Optional

# --------------------------------------------------------------------------
# 1) 默认可执行文件白名单
#    刻意**不含** sh / bash / zsh / cmd / powershell / env / xargs / eval —— 它们
#    能把任意字符串再解释一次，等于白名单形同虚设。也不含 sudo / su / chmod /
#    chown / systemctl / docker / kubectl / crontab / ssh / nc 等提权与横向移动工具。
# --------------------------------------------------------------------------
DEFAULT_SHELL_ALLOWLIST: tuple[str, ...] = (
    # 只读查看
    "ls", "dir", "cat", "type", "head", "tail", "wc", "sort", "uniq", "cut",
    "grep", "findstr", "echo", "pwd", "cd", "date", "whoami", "df", "du", "stat",
    "which", "readlink", "realpath",
    "find", "tree", "diff", "md5sum", "sha256sum",
    # 文件操作（受 cwd=sandbox 约束）
    "cp", "mv", "mkdir", "touch", "tar", "zip", "unzip", "gzip", "gunzip",
    # 运行时
    "python", "python3", "pip", "pip3", "node", "npm", "npx", "java", "go",
    # 版本管理 / 传输（出网另受 security.outbound_allow 约束）
    "git", "curl", "wget",
    # 包管理 / 文本处理（内网可信部署常用；高危组合仍受拒绝清单约束）
    "apt", "apt-get", "sed", "awk",
)

# --------------------------------------------------------------------------
# 2) 拒绝清单：命中即拒，优先级高于白名单（白名单内的命令也可能被恶意组合）
# --------------------------------------------------------------------------
_DENY_RULES: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-zA-Z]+\s+)*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b",
     "递归强删（rm -rf）"),
    (r"\brm\s+.*\s+/(\s|$)", "删除根目录"),
    (r"\b(mkfs(\.\w+)?|fdisk|parted|diskpart)\b", "磁盘格式化/分区"),
    (r"\bdd\s+if=", "裸设备读写（dd）"),
    (r">\s*/dev/(sd|nvme|hd|mapper)", "写入块设备"),
    (r"\b(shutdown|reboot|halt|poweroff|init\s+[06])\b", "关机/重启"),
    (r":\s*\(\s*\)\s*\{.*\|.*&\s*\}\s*;?\s*:", "fork 炸弹"),
    (r"\b(sudo|su|doas|runas)\b", "提权"),
    (r"\bchmod\s+(-R\s+)?[0-7]*777\b", "开放 777 权限"),
    (r"\b(chown|chgrp)\b", "变更属主"),
    (r"\b(useradd|usermod|userdel|passwd|adduser|net\s+user)\b", "账号操作"),
    (r"\b(systemctl|service|launchctl|sc\s+(config|delete|stop|start))\b", "服务管理"),
    (r"\b(iptables|firewall-cmd|netsh|ufw)\b", "防火墙/网络策略"),
    (r"\b(crontab|schtasks|at)\b", "系统级定时任务（请用智枢定时任务）"),
    (r"\b(ssh|scp|sftp|telnet|nc|ncat|netcat|socat)\b", "远程会话/隧道"),
    (r"\b(docker|podman|kubectl|nerdctl)\b", "容器/编排控制"),
    (r"\breg\s+(delete|add)\b|\bregedit\b", "注册表写入"),
    (r"\b(format|rd|rmdir)\s+/[sq]", "Windows 强制删除/格式化"),
    (r"\bdel\s+/[sfq]", "Windows 递归强删"),
    (r"/etc/(passwd|shadow|sudoers)", "读写系统账号文件"),
    (r"(^|[\s/\\])\.ssh([\s/\\]|$)|id_rsa|authorized_keys", "SSH 凭据"),
    (r"\b(env|printenv|set)\b\s*($|[|>])", "导出环境变量（防密钥外传）"),
    (r"\b(curl|wget)\b[^|;]*\|\s*(ba|z|d)?sh\b", "远程脚本直接执行（curl | sh）"),
    (r"\bbase64\b[^|;]*\|\s*(ba|z|d)?sh\b", "编码载荷执行"),
    (r"\b(eval|exec)\s", "动态求值"),
    (r"\bnohup\b|\bdisown\b|\bsetsid\b", "脱离进程组常驻"),
)

_DENY_COMPILED = tuple((re.compile(p, re.IGNORECASE), why) for p, why in _DENY_RULES)

# 命令替换 / 进程替换：能把任意程序塞进白名单命令的参数里，直接禁用
_SUBSTITUTION = re.compile(r"\$\(|`|<\(|>\(|\$\{[^}]*\|")

# 段切分交由 `_split_segments`（引号感知）+ `_strip_heredocs`（剥离 heredoc 体）
# 处理，避免多行脚本 / 引号内 ; & | \n 被误判为命令分隔符（原 `_SEGMENT_SPLIT`
# 裸切段会把 `python3 -c "a; b"` 拆断、把 heredoc 正文逐行当命令，误杀合法脚本）。

# 环境变量前缀赋值（FOO=bar cmd）—— 会被 shell 用来改 PATH/LD_PRELOAD 后再调白名单命令
_ENV_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# 环境变量白名单：只透传运行必需项，其余（尤其密钥）一律不进子进程
_ENV_KEEP = (
    "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP",
    "SystemRoot", "COMSPEC", "PATHEXT", "WINDIR", "USERPROFILE", "NUMBER_OF_PROCESSORS",
)
_ENV_SECRET_PAT = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|_KEY$|APIKEY|API_KEY|CREDENTIAL|SESSION|COOKIE|ZHISHU_)",
    re.IGNORECASE,
)


def _base_name(token: str) -> str:
    """取可执行文件基名（去路径、去 .exe、去引号）。"""
    t = token.strip().strip('"\'')
    t = t.replace("\\", "/").rsplit("/", 1)[-1]
    if t.lower().endswith(".exe"):
        t = t[:-4]
    return t.lower()


# heredoc 操作符：<< / <<- 后跟分隔符（可引号包裹）。here-string（<<<）不匹配。
_HEREDOC_OP = re.compile(r"<<-?\s*(?P<delim>['\"]?[A-Za-z_][A-Za-z0-9_]*['\"]?)")


def _strip_heredocs(text: str) -> str:
    """剥离 heredoc（<< / <<-）多行体，使正文不被当成命令逐段校验。

    here-string（<<<）是单行，不处理；>> 是追加重定向，不是 heredoc。
    剥离后仍保留 `cmd <<DELIM` 这一行（首 token 如 python3 仍需白名单校验）。
    注意：本函数只动段切分用的副本；拒绝清单 / 命令替换仍对**完整原文**检查，
    因此 heredoc 正文里的 `rm -rf /` 等高危内容一样会被拦下。
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _HEREDOC_OP.search(line)
        if m:
            delim = m.group("delim").strip().strip("'\"")
            out.append(line)
            i += 1
            while i < n:
                body = lines[i].rstrip("\r\n")
                if body.lstrip("\t") == delim or body == delim:
                    break
                i += 1
            # 跳过分隔符所在行（若文件未正常结束则保留到末尾，不越界）
            if i < n:
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _split_segments(text: str) -> list[str]:
    """引号感知的段切分：仅在引号**外**按 shell 控制算符切段。

    引号（单/双）内的 ; & | || && \\n 视为数据，不当分隔符——这正是修复
    `python3 -c "多行脚本"` 被误判『引号不闭合』的根因。双字符算符 || && 优先于
    单字符切分。返回非空（strip 后）的段列表，供 `check_command` 逐段校验首 token。
    """
    segments: list[str] = []
    cur: list[str] = []
    quote: Optional[str] = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            cur.append(ch)
            i += 1
            continue
        two = text[i:i + 2]
        if two in ("||", "&&"):
            if cur:
                segments.append("".join(cur))
            cur = []
            i += 2
            continue
        if ch in (";", "|", "&", "\n"):
            if cur:
                segments.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        segments.append("".join(cur))
    return segments


def check_command(cmd: str, allowlist: Optional[Iterable[str]] = None,
                  enforce_allowlist: bool = True) -> Optional[str]:
    """校验命令。放行返回 None，拒绝返回中文原因（可直接回给用户）。"""
    text = (cmd or "").strip()
    if not text:
        return "命令为空"
    if len(text) > 4000:
        return "命令过长（>4000 字符）"
    if "\x00" in text:
        return "命令包含空字节"

    for pat, why in _DENY_COMPILED:
        if pat.search(text):
            return f"命中高危规则：{why}"

    if not enforce_allowlist:
        return None

    if _SUBSTITUTION.search(text):
        return "禁止命令替换 / 进程替换（$( ) 、反引号、<( )）"

    allow = {c.lower() for c in (allowlist or DEFAULT_SHELL_ALLOWLIST)}
    cleaned = _strip_heredocs(text)
    for seg in _split_segments(cleaned):
        seg = seg.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg, posix=(os.name != "nt"))
        except ValueError:
            return "命令引号不闭合，无法安全解析"
        if not tokens:
            continue
        head = tokens[0]
        if _ENV_PREFIX.match(head):
            return "禁止以环境变量前缀（VAR=值 命令）方式执行"
        base = _base_name(head)
        if base not in allow:
            return (f"命令 {base} 不在白名单内。允许的命令："
                    f"{'、'.join(sorted(allow)[:20])}…（可在 security.shell_allowlist 调整）")
    return None


def sandbox_env(extra: Optional[dict] = None) -> dict:
    """构造最小化子进程环境：保留运行必需项，剔除全部密钥类变量。"""
    env: dict[str, str] = {}
    for k in _ENV_KEEP:
        v = os.environ.get(k)
        if v:
            env[k] = v
    env.setdefault("PATH", os.defpath)
    # 防止子进程读到宿主的 Python 配置 / 站点包污染
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    for k, v in (extra or {}).items():
        if _ENV_SECRET_PAT.search(k):
            continue
        env[str(k)] = str(v)
    return env


def _rlimit_preexec(mem_mb: int, cpu_sec: int, fsize_mb: int):
    """POSIX 资源上限 + 独立会话（便于超时整组击杀）。Windows 返回 None。"""
    if os.name == "nt":
        return None

    def _apply():  # pragma: no cover —— 仅 POSIX 子进程内执行
        import resource

        os.setsid()
        if mem_mb > 0:
            lim = mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
        if cpu_sec > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec))
        if fsize_mb > 0:
            lim = fsize_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (lim, lim))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        except (ValueError, OSError):
            pass

    return _apply


async def run_guarded(cmd: str, cwd: str, timeout: int = 60,
                      max_output: int = 4000, mem_mb: int = 1024,
                      fsize_mb: int = 256) -> str:
    """在受限子进程中执行命令；超时整组击杀，输出解码并截断。

    调用方须**先**过 `check_command()`，本函数只负责隔离与回收。
    """
    os.makedirs(cwd, exist_ok=True)
    kwargs: dict = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
        "stdin": asyncio.subprocess.DEVNULL,
        "cwd": cwd,
        "env": sandbox_env(),
    }
    pre = _rlimit_preexec(mem_mb, max(1, timeout), fsize_mb)
    if pre is not None:
        kwargs["preexec_fn"] = pre
    elif os.name == "nt":
        # Windows：独立进程组，便于超时时整组终止
        kwargs["creationflags"] = getattr(__import__("subprocess"),
                                          "CREATE_NEW_PROCESS_GROUP", 0)

    proc = await asyncio.create_subprocess_shell(cmd, **kwargs)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _kill_tree(proc)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        return f"[超时] 命令执行超过 {timeout} 秒，已终止整个进程组。"
    raw = out or b""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, AttributeError):
            continue
    else:  # pragma: no cover
        text = str(raw)
    text = text.strip()
    if len(text) > max_output:
        text = text[:max_output] + f"\n…（输出已截断，共 {len(text)} 字符）"
    return text


def _kill_tree(proc) -> None:
    """尽最大努力终止子进程及其派生的孙进程（shell 场景必须整组杀）。"""
    try:
        if os.name != "nt":
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        if os.name == "nt":
            os.system(f"taskkill /F /T /PID {proc.pid} >NUL 2>&1")  # noqa: S605
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "DEFAULT_SHELL_ALLOWLIST", "check_command", "sandbox_env", "run_guarded",
]

if sys.version_info < (3, 9):  # pragma: no cover
    raise RuntimeError("智枢要求 Python 3.9+")
