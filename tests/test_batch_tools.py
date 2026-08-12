#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：4 个文件处理工具的批量参数（v1.0.21 提速关键）。

把此前在容器内临时验证的逻辑固化为可入库测试，确保批量参数行为不被回归：
  * read_file     -> paths 列表（多文件带分隔头拼接返回）
  * terminal_run  -> commands 列表（合并为单次执行，共享一次快照/进程）
  * code_exec     -> snippets 列表（单子进程内顺序执行，变量跨段保留，消除多次冷启动）
  * generate_excel-> files 列表（多工作簿，每个返回 /media 下载链接）

直接运行：  python tests/test_batch_tools.py
也可被 pytest 收集（同步 test_* 函数，无需 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

# ── 让脚本在容器内（/app/backend 在 PYTHONPATH）或仓库根运行都能 import zhishu ──
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in ("/app/backend", os.path.join(_HERE, "..", "backend")):
    _cand = os.path.abspath(_cand)
    if os.path.isdir(os.path.join(_cand, "zhishu")) and _cand not in sys.path:
        sys.path.insert(0, _cand)


# --------------------------------------------------------------------------
# 进程内 fake 装置（不触碰真实配置 / 媒体库）
# --------------------------------------------------------------------------
class FakeSecurityCode:
    allow_code_exec = True
    code_exec_timeout = 30
    code_exec_mem_limit_mb = 0
    code_exec_network_isolated = False


class FakeSecurityShell:
    allow_shell = True
    shell_allowlist = None
    shell_enforce_allowlist = False  # 仅走高危拦截，放行白名单外的只读命令


class FakeSecurityShellEnforce:
    allow_shell = True
    shell_allowlist = None
    shell_enforce_allowlist = True   # 同时走白名单 + 高危拦截


class FakeMedia:
    """极简媒体库替身：落盘到临时目录并返回 /media/... URL，记录已保存文件。"""
    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.saved = {}  # basename -> 绝对路径

    def save_file(self, src_path, kind=None, owner=None):
        owner = owner or "tester"
        odir = os.path.join(self.root, owner)
        os.makedirs(odir, exist_ok=True)
        dst = os.path.join(odir, os.path.basename(src_path))
        shutil.copyfile(src_path, dst)
        self.saved[os.path.basename(dst)] = dst
        return f"/media/{owner}/{os.path.basename(dst)}"


def _make_ctx(security=None, media=None, user="tester", role="user"):
    from zhishu.core.tools.base import ToolContext
    return ToolContext(user=user, user_role=role, is_admin=False,
                       security=security, media=media)


def _install_fake_ctx(data_dir, store_dir="media"):
    """替换全局 get_ctx，让 read_file 的 _resolve_read_path 拿到可控的 media_root。"""
    import zhishu.context as ctxmod
    fake = SimpleNamespace(cfg=SimpleNamespace(
        server=SimpleNamespace(data_dir=data_dir),
        media=SimpleNamespace(store_dir=store_dir),
    ))
    ctxmod.get_ctx = lambda: fake
    return fake


# --------------------------------------------------------------------------
# 各工具检查
# --------------------------------------------------------------------------
async def check_read_file(owner="tester"):
    from zhishu.core.tools.builtins.file import read_file

    tmp = tempfile.mkdtemp(prefix="zh_read_")
    try:
        data_dir = os.path.join(tmp, "data")
        media_root = os.path.join(data_dir, "media")
        os.makedirs(os.path.join(media_root, owner), exist_ok=True)
        _install_fake_ctx(data_dir, "media")

        fa = os.path.join(media_root, owner, "a.txt")
        with open(fa, "w", encoding="utf-8") as f:
            f.write("alpha line1\nalpha line2")
        fb = os.path.join(media_root, owner, "b.txt")
        with open(fb, "w", encoding="utf-8") as f:
            f.write("beta only")

        ctx = _make_ctx()
        # 单文件向后兼容：不带分隔头
        single = await read_file({"path": f"/media/{owner}/a.txt"}, ctx)
        assert "alpha line1" in single, single
        assert "===== 文件" not in single, single

        # 批量 paths：带分隔头拼接
        multi = await read_file(
            {"paths": [f"/media/{owner}/a.txt", f"/media/{owner}/b.txt"]}, ctx)
        assert "===== 文件 a.txt =====" in multi, multi
        assert "===== 文件 b.txt =====" in multi, multi
        assert "alpha line1" in multi and "beta only" in multi, multi

        # 缺失文件：友好报错而非崩溃
        miss = await read_file({"paths": [f"/media/{owner}/nope.txt"]}, ctx)
        assert "文件不存在或越权" in miss, miss
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def check_terminal():
    from zhishu.core.tools.builtins.terminal import terminal_run

    # 批量 commands：合并为单次执行
    ctx = _make_ctx(security=FakeSecurityShell(), media=None)
    out = await terminal_run({"commands": ["echo hello", "echo world"]}, ctx)
    assert "hello" in out and "world" in out, out

    # 单命令向后兼容
    one = await terminal_run({"command": "echo solo"}, ctx)
    assert "solo" in one, one

    # 批量路径下安全门依然生效：高危命令被拦截
    blocked = await terminal_run(
        {"commands": ["rm -rf /"], "timeout": 10},
        _make_ctx(security=FakeSecurityShellEnforce(), media=None),
    )
    assert "[已拦截]" in blocked, blocked


async def check_code_exec():
    from zhishu.core.tools.builtins.code_exec import code_exec

    ctx = _make_ctx(security=FakeSecurityCode(), media=None)
    # snippets：单子进程顺序执行，变量跨段保留
    out = await code_exec({"snippets": ["x = 21 * 2", "print('RESULT', x)"]}, ctx)
    assert "RESULT 42" in out, out

    # 单 code 向后兼容
    one = await code_exec({"code": "print('SOLO_CODE')"}, ctx)
    assert "SOLO_CODE" in one, one


async def check_generate_excel():
    from zhishu.core.tools.builtins.generate_excel import generate_excel
    import openpyxl

    tmp = tempfile.mkdtemp(prefix="zh_xlsx_")
    try:
        media = FakeMedia(os.path.join(tmp, "media"))
        ctx = _make_ctx(media=media)

        # 批量 files：多工作簿，每个返回 /media 链接
        out = await generate_excel({"files": [
            {"filename": "a.xlsx", "sheets": [{"name": "S", "rows": [["x", "y"], [1, 2]]}]},
            {"filename": "b.xlsx", "sheets": [{"name": "T", "rows": [["p"], [3]]}]},
        ]}, ctx)
        assert out.count("已生成标准 Excel 文件") == 2, out
        assert "/media/tester/" in out, out

        # 校验真实生成的文件可被 openpyxl 打开（确为合法 xlsx，非损坏）
        assert len(media.saved) == 2, media.saved
        for p in media.saved.values():
            wb = openpyxl.load_workbook(p)
            assert wb.sheetnames, p

        # 单工作簿向后兼容
        one = await generate_excel(
            {"filename": "c.xlsx", "sheets": [{"name": "U", "rows": [["q"]]}]}, ctx)
        assert "已生成标准 Excel 文件「c.xlsx」" in one, one
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def main():
    await check_read_file()
    await check_terminal()
    await check_code_exec()
    await check_generate_excel()
    print("ALL_TESTS_PASSED")


def test_batch_read_file():
    asyncio.run(check_read_file())


def test_batch_terminal():
    asyncio.run(check_terminal())


def test_batch_code_exec():
    asyncio.run(check_code_exec())


def test_batch_generate_excel():
    asyncio.run(check_generate_excel())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print("TEST_FAILED:", e)
        sys.exit(1)
    sys.exit(0)
