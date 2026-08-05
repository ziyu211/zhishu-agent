"""媒体 MIME 单一真源 + RFC 5987 Content-Disposition 单测（借鉴 Hermes 设计）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.media import media_mime, content_disposition, resolve_media_fallback  # noqa: E402


def check(cond, msg):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {msg}")
    assert cond, msg


def _build_tree(root):
    """构造授权目录：alice 与 admin 各自有文件 + attachments 子目录。"""
    os.makedirs(os.path.join(root, "alice"), exist_ok=True)
    os.makedirs(os.path.join(root, "attachments", "alice", "r1"), exist_ok=True)
    os.makedirs(os.path.join(root, "attachments", "bob"), exist_ok=True)
    with open(os.path.join(root, "alice", "report.txt"), "w") as f:
        f.write("a")
    with open(os.path.join(root, "attachments", "alice", "r1", "fw_log (0626).csv"), "w") as f:
        f.write("a" * 10)
    with open(os.path.join(root, "attachments", "bob", "secret.txt"), "w") as f:
        f.write("x")


def test_media_mime_single_source():
    print("\n[1] media_mime 单一真源映射")
    check(media_mime("a.csv") == "text/csv; charset=utf-8", "csv -> text/csv")
    check(media_mime("a.CSV") == "text/csv; charset=utf-8", "CSV 大小写不敏感")
    check(media_mime("a.png") == "image/png", "png -> image/png")
    check(media_mime("a.pdf") == "application/pdf", "pdf -> application/pdf")
    check(media_mime("a.xlsx").startswith("application/vnd"), "xlsx -> ooxml")
    # 未知扩展名回退 octet-stream（不抛异常、不返回 None）
    check(media_mime("a.unknownext") == "application/octet-stream", "未知扩展名回退")


def test_content_disposition_utf8():
    print("\n[2] content_disposition RFC 5987 UTF-8 文件名")
    d = content_disposition("报告.csv")
    check(d.startswith("attachment; "), "以 attachment 开头")
    check("filename*=UTF-8''" in d, "包含 RFC 5987 UTF-8 段")
    # 中文被百分号编码，不应以明文中文出现
    check("%" in d and "报告" not in d, "中文被 URL 编码")
    # ASCII 兜底名存在
    check('filename="download"' in d or 'filename="' in d, "包含 ASCII 兜底名")


def test_resolve_media_fallback():
    print("\n[3] resolve_media_fallback 容错回退")
    import tempfile
    root = tempfile.mkdtemp()
    try:
        _build_tree(root)
        admin = {"u": "admin", "r": "admin"}
        alice = {"u": "alice", "r": "user"}

        # 3.1 缺扩展名回退：链接 'fw_log' → 命中 'fw_log (0626).csv'
        hit = resolve_media_fallback(
            "/media/attachments/alice/r1/fw_log", admin, root, 200 * 1024 * 1024)
        check(hit is not None and hit.endswith("fw_log (0626).csv"),
              "缺扩展名/空格回退命中真实文件")

        # 3.2 缺 owner 段回退：/media/report.txt（无 owner）→ admin 在 alice 目录命中
        hit2 = resolve_media_fallback("/media/report.txt", admin, root, 200 * 1024 * 1024)
        check(hit2 is not None and hit2.endswith(os.path.join("alice", "report.txt")),
              "缺 owner 段回退命中授权目录文件")

        # 3.3 普通用户 alice 查不到 bob 的文件（不跨用户泄露）
        hit3 = resolve_media_fallback("/media/secret.txt", alice, root, 200 * 1024 * 1024)
        check(hit3 is None, "普通用户无法回退命中他人文件")

        # 3.4 alice 能命中自己的文件
        hit4 = resolve_media_fallback("/media/report.txt", alice, root, 200 * 1024 * 1024)
        check(hit4 is not None and hit4.endswith(os.path.join("alice", "report.txt")),
              "本人可回退命中自身文件")

        # 3.5 文件真不存在 → None（诚实 404）
        hit5 = resolve_media_fallback("/media/nope_xyz.txt", admin, root, 200 * 1024 * 1024)
        check(hit5 is None, "不存在文件返回 None")

        # 3.6 超大小限制 → None
        with open(os.path.join(root, "alice", "big.bin"), "w") as f:
            f.write("z" * 50)
        hit6 = resolve_media_fallback("/media/big.bin", admin, root, 10)
        check(hit6 is None, "超出大小上限返回 None")
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_media_mime_single_source()
    test_content_disposition_utf8()
    test_resolve_media_fallback()
    print("\nALL MEDIA SERVE TESTS PASSED")
