"""引用路径重发布（publish_referenced_paths）回归测试。

验证「模型把产物写到 cwd/输出目录之外、却把内部绝对路径回显给用户」时，
publish_referenced_paths 能把这些真实文件统一补出 /media 下载链接：

  1. 文件落在媒体根（data_dir/generated）内 → 直接改写为 /media 链接，零拷贝、零重复。
  2. 文件落在媒体根之外（如 /tmp、挂载卷）→ 拷贝到媒体库并发布 /media 链接。
  3. 不存在的泄漏路径 → 不发布、文本不被破坏。
  4. append_unique_links 对文件名已出现的链接去重，避免与 publish_diff 重复刷屏。
  5. 集成：code_exec 把文件写到 media_root/attachments/<owner>/... 时，结果自动含 /media 链接。

全部使用临时目录，不触碰生产数据。运行：
  python backend/tests/test_artifact_referenced.py
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
from zhishu.core.tools.builtins.artifacts import (  # noqa: E402
    publish_referenced_paths,
    append_unique_links,
)
from zhishu.core.tools.builtins.code_exec import code_exec  # noqa: E402


def _media():
    root = tempfile.mkdtemp(prefix="zh_media_")
    return MediaStore(root)


def _ctx(media, *, role="admin", allow_code=True):
    sec = types.SimpleNamespace(
        allow_code_exec=allow_code,
        allow_shell=True,
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


def test_rewrite_inside_media_root():
    print("\n[1] 媒体根内文件 → 改写为 /media 链接（零拷贝）")
    m = _media()
    # 模拟模型直接写到媒体根下的 attachments/<owner>/ 路径
    target_dir = os.path.join(m.root, "attachments", "alice")
    os.makedirs(target_dir, exist_ok=True)
    fpath = os.path.join(target_dir, "report.txt")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("hello")
    before_files = sum(len(fs) for _, _, fs in os.walk(m.root))
    text = f"报告已生成：{fpath}，请下载。"
    out, refs = publish_referenced_paths(
        text, m, "alice", media_root=m.root, sandbox_root=_SB, out_dir=None)
    # 改写为 /media/attachments/alice/report.txt
    check("/media/attachments/alice/report.txt" in out,
          f"绝对路径被改写为 /media 链接: {out}")
    check(any(u == "/media/attachments/alice/report.txt" for _, u in refs),
          "refs 含正确链接")
    after_files = sum(len(fs) for _, _, fs in os.walk(m.root))
    check(after_files == before_files, "未产生额外拷贝文件（零重复）")


def test_copy_outside_media_root():
    print("\n[2] 媒体根外真实文件 → 拷贝发布 /media 链接")
    m = _media()
    ext = tempfile.mkdtemp(prefix="zh_ext_")
    fpath = os.path.join(ext, "data.csv")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("a,b\n1,2\n")
    text = f"结果在 {fpath} 自行获取"
    out, refs = publish_referenced_paths(
        text, m, "alice", media_root=m.root, sandbox_root=_SB, out_dir=None)
    check(any(u.startswith("/media/alice/") and u.endswith("data.csv") for _, u in refs),
          f"媒体根外文件被拷贝发布: {refs}")
    # 落盘成功
    saved = os.path.join(m.root, "alice", os.path.basename(refs[0][1].split("/")[-1]))
    check(os.path.isfile(saved), "拷贝后的文件已落盘到 owner 目录")
    check("/media/alice/" in out, "文本中出现 /media 链接")


def test_nonexistent_leak_ignored():
    print("\n[3] 不存在的泄漏路径 → 不发布、不破坏文本")
    m = _media()
    text = "文件在 /tmp/does_not_exist_12345.csv 请下载"
    out, refs = publish_referenced_paths(
        text, m, "alice", media_root=m.root, sandbox_root=_SB, out_dir=None)
    check(refs == [], "无引用被发布")
    check(out == text, "原文本未被改动")


def test_append_unique_links_dedup():
    print("\n[4] append_unique_links 按文件名去重")
    text = "已生成 [report.csv](/media/alice/report.csv)"
    refs = [("report.csv", "/media/alice/report_1.csv"),
            ("other.csv", "/media/alice/other.csv")]
    out = append_unique_links(text, refs)
    check("/media/alice/report_1.csv" not in out, "文件名已出现的链接被去重")
    check("/media/alice/other.csv" in out, "新文件名链接被追加")


def test_code_exec_outside_cwd_publish():
    print("\n[5] 集成：code_exec 写到 media_root 内 → 结果自动含 /media 链接")
    m = _media()
    ctx = _ctx(m, role="operator")
    code = (
        "import os\n"
        f"p = os.path.join({m.root!r}, 'attachments', 'alice', 'via_code.txt')\n"
        "os.makedirs(os.path.dirname(p), exist_ok=True)\n"
        "with open(p, 'w') as f:\n"
        "    f.write('generated')\n"
        "print('wrote', p)\n"
    )
    res = asyncio.run(code_exec({"code": code}, ctx))
    check("wrote" in res, "代码输出正常返回")
    check("/media/attachments/alice/via_code.txt" in res,
          f"写到媒体根内的文件被自动发布为 /media 链接: {res[:160]}")


if __name__ == "__main__":
    test_rewrite_inside_media_root()
    test_copy_outside_media_root()
    test_nonexistent_leak_ignored()
    test_append_unique_links_dedup()
    test_code_exec_outside_cwd_publish()
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")
