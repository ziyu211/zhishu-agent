"""自动发布（auto-publish）回归测试。

验证：
  1. MediaStore.save_file 保留 slug 化原文件名，并按 owner 隔离。
  2. artifacts.snapshot/publish_diff 只发布「新增/修改」文件，跳过未变化、
     排除前缀、超大文件，且数量受上限保护。
  3. file_write 默认把文件发布为 /media 下载链接（不再只给沙箱路径）。
  4. code_exec 在沙箱产生的新文件被自动发布（无需 save_output 参数）。
  5. make_downloadable 能把已存在的沙箱文件兜底转成 /media 链接。

全部使用临时目录，不触碰生产数据。运行：
  PYTHONPATH=backend python backend/tests/test_artifact_autopublish.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

PASS = 0
FAIL: list[str] = []


def check(cond: bool, name: str) -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}")


# 在任何 builtins 导入前把沙箱指到临时目录，避免污染仓库 data/sandbox
_SB = tempfile.mkdtemp(prefix="zh_sb_")
os.environ["ZHISHU_SANDBOX"] = _SB

from zhishu.core.media import MediaStore  # noqa: E402
from zhishu.core.tools.builtins.artifacts import snapshot, publish_diff  # noqa: E402
from zhishu.core.tools.builtins.file import file_write, make_downloadable  # noqa: E402
from zhishu.core.tools.builtins.code_exec import code_exec  # noqa: E402


def _media():
    root = tempfile.mkdtemp(prefix="zh_media_")
    return MediaStore(root)


def _ctx(media, *, role="admin", allow_code=True, allow_shell=True):
    sec = types.SimpleNamespace(
        allow_code_exec=allow_code,
        allow_shell=allow_shell,
        code_exec_timeout=30,
        code_exec_mem_limit_mb=0,
        shell_mem_limit_mb=1024,
        shell_allowlist=None,
        shell_enforce_allowlist=True,
        outbound_allow=False,
    )
    return types.SimpleNamespace(
        kb=None, security=sec, user="alice", session="s1",
        is_admin=(role == "admin"), user_role=role, agent_name="",
        media=media,
    )


def test_media_save_file():
    print("\n[1] MediaStore.save_file 保留原文件名 + owner 隔离")
    m = _media()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "IP_20.1.106.250_log.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write("a,b,c\n1,2,3\n")
    url = m.save_file(p, kind="file", owner="alice")
    check(url.startswith("/media/alice/"), f"URL 带 owner 前缀: {url}")
    check(url.endswith("IP_20.1.106.250_log.csv"), "URL 保留原始文件名")
    # 落盘成功
    on_disk = os.path.join(m.root, "alice", os.path.basename(url.split("/")[-1]))
    check(os.path.isfile(on_disk), "文件已落盘到 owner 目录")
    # 特殊字符被 slug 化
    p2 = os.path.join(d, "weird name/../name &.txt")
    with open(p2, "w") as f:
        f.write("x")
    url2 = m.save_file(p2, kind="file", owner="alice")
    check("/" not in url2.split("/media/alice/")[-1], "路径穿越被归一化")


def test_publish_diff():
    print("\n[2] artifacts snapshot/publish_diff 差分发布")
    m = _media()
    sb = tempfile.mkdtemp()
    # 历史文件（执行前应存在，不应被发布）
    with open(os.path.join(sb, "preexisting.txt"), "w") as f:
        f.write("old")
    before = snapshot(sb)
    # 新增文件
    with open(os.path.join(sb, "new.csv"), "w") as f:
        f.write("1,2,3")
    # 修改历史文件
    with open(os.path.join(sb, "preexisting.txt"), "w") as f:
        f.write("changed")
    # 排除前缀临时文件
    with open(os.path.join(sb, "zh_out_tmp.bin"), "w") as f:
        f.write("tmp")
    out = publish_diff(sb, before, m, "bob")
    check("[new.csv](/media/bob/" in out, "新增文件被发布")
    check("preexisting.txt" in out, "被修改的历史文件被发布")
    check("zh_out_tmp.bin" not in out, "排除前缀临时文件未发布")
    # 不变文件不重复发布：再跑一次应为空
    before2 = snapshot(sb)
    out2 = publish_diff(sb, before2, m, "bob")
    check(out2 == "", "无新增/修改时返回空串")


def test_publish_diff_size_limit():
    print("\n[3] 超大文件被跳过并提示")
    m = _media()
    sb = tempfile.mkdtemp()
    before = snapshot(sb)
    with open(os.path.join(sb, "huge.bin"), "wb") as f:
        f.write(b"\0" * (101 * 1024 * 1024))  # > 100MB
    out = publish_diff(sb, before, m, "bob")
    check("超过大小上限" in out, "超大文件触发提示")


def test_file_write_default_publish():
    print("\n[4] file_write 默认发布为 /media 下载链接")
    m = _media()
    ctx = _ctx(m)
    rel = "report/output.csv"
    content = "col1,col2\nhello,world\n"
    res = asyncio.run(file_write({"path": rel, "content": content}, ctx))
    check("[output.csv](/media/alice/" in res, f"返回 /media 链接: {res[:80]}")
    check("/media/alice/" in res, "链接走媒体库而非沙箱路径")


def test_code_exec_autopublish():
    print("\n[5] code_exec 沙箱新文件自动发布（无需 save_output）")
    m = _media()
    ctx = _ctx(m, role="operator")
    code = (
        "import os\n"
        "with open('gen_result.csv','w') as f:\n"
        "    f.write('x,y\\n1,2\\n')\n"
        "print('done')\n"
    )
    res = asyncio.run(code_exec({"code": code}, ctx))
    check("done" in res, "代码输出正常返回")
    check("gen_result.csv" in res and "/media/" in res, f"新文件自动发布: {res[:120]}")


def test_make_downloadable():
    print("\n[6] make_downloadable 兜底转 /media 链接")
    import zhishu.core.tools.builtins.file as file_mod
    m = _media()
    ctx = _ctx(m)
    # 先写一个沙箱文件
    sb_file = os.path.join(_SB, "manual.txt")
    with open(sb_file, "w", encoding="utf-8") as f:
        f.write("manual content")
    # _resolve_read_path 依赖 app 上下文（get_ctx），单测中替换为直接返回沙箱绝对路径
    orig = file_mod._resolve_read_path
    file_mod._resolve_read_path = lambda p, owner=None, is_admin=False: os.path.join(_SB, os.path.basename(p))
    try:
        res = asyncio.run(make_downloadable({"path": "manual.txt"}, ctx))
        check("manual.txt" in res and "/media/alice/" in res, f"兜底生成链接: {res[:80]}")
        # 已是 /media 的返回原样
        res2 = asyncio.run(make_downloadable({"path": "/media/alice/x.csv"}, ctx))
        check("/media/alice/x.csv" in res2, "已是 /media 链接则原样返回")
    finally:
        file_mod._resolve_read_path = orig


if __name__ == "__main__":
    test_media_save_file()
    test_publish_diff()
    test_publish_diff_size_limit()
    test_file_write_default_publish()
    test_code_exec_autopublish()
    test_make_downloadable()
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")
