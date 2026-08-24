"""P2 发布协议/未知类型/后端插件化回归测试（v1.0.39）。

覆盖：
  A. MEDIA: 发布协议（download_guard.process_media_tags）：
       - extract_media_tags 提取；
       - 已落媒体根的路径直接改写 /media 链接（零拷贝）；
       - 其它真实存在路径拷贝发布；
       - 不存在路径 → 保留原文 + 说明（绝不编造链接）；
       - 无媒体库 → 原样返回。
  B. 记忆后端插件化（backends.register_memory_backend / create_memory_backend）：
       - 注册自定义后端并启用；
       - 未注册名字回退 builtin；
       - 插件构造失败回退 builtin。

运行：python tests/test_p2_media_protocol_backends.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.agent import download_guard  # noqa: E402
from zhishu.core.memory import backends  # noqa: E402

PASS = 0
FAIL = []


def check(cond, name):
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}")


class _FakeMedia:
    def __init__(self, root: str):
        self.root = root
        self.url_prefix = "/media"
        self.saved = []

    def save_file(self, src_path: str, kind: str = "file", owner: str = None) -> str:
        os.makedirs(self.root, exist_ok=True)
        name = f"f_{len(self.saved)}_{os.path.basename(src_path)}"
        shutil.copy(src_path, os.path.join(self.root, name))
        self.saved.append(name)
        return f"{self.url_prefix}/{name}"


def test_media_tag_extract():
    print("\n[A1] extract_media_tags 提取")
    tags = download_guard.extract_media_tags(
        "报告已生成 MEDIA:/tmp/a.csv 与 MEDIA:/tmp/b.xlsx 及 MEDIA: /tmp/c.txt")
    check(len(tags) == 3, f"提取 3 个 MEDIA 标签（{tags}）")
    check(download_guard.extract_media_tags("无标签文本") == [], "无标签返回空")


def test_media_tag_publish():
    print("\n[A2] process_media_tags 发布")
    tmp = tempfile.mkdtemp()
    try:
        # 已落媒体根的路径 → 直接改写
        media_root = os.path.join(tmp, "generated")
        os.makedirs(media_root, exist_ok=True)
        in_media = os.path.join(media_root, "report.csv")
        with open(in_media, "w", encoding="utf-8") as f:
            f.write("a,b\n1,2\n")
        media = _FakeMedia(tmp)  # root 与 media_root 不同目录以区分拷贝
        text = f"结果文件：MEDIA:{in_media}"
        out, pub = download_guard.process_media_tags(
            text, media, "alice", media_root=media_root)
        check("/media/" in out and "report.csv" in out, f"媒体根路径改写为链接（{out}）")
        check(any("report.csv" == n for n, _ in pub), "发布列表含 report.csv")

        # 其它真实存在路径 → 拷贝发布
        outside = os.path.join(tmp, "scratch", "out.txt")
        os.makedirs(os.path.dirname(outside), exist_ok=True)
        with open(outside, "w", encoding="utf-8") as f:
            f.write("hello")
        out2, pub2 = download_guard.process_media_tags(
            f"见 MEDIA:{outside}", media, "alice", media_root=media_root)
        check("/media/" in out2, f"外部路径拷贝发布（{out2}）")

        # 不存在路径 → 说明而非编造
        out3, pub3 = download_guard.process_media_tags(
            "MEDIA:/nonexistent/x.csv", media, "alice", media_root=media_root)
        check("文件不存在" in out3 and "/media/" not in out3,
              f"不存在路径不编造链接（{out3}）")

        # 无媒体库 → 原样
        out4, pub4 = download_guard.process_media_tags(
            f"MEDIA:{outside}", None, "alice", media_root=media_root)
        check(out4 == f"MEDIA:{outside}" and pub4 == [], "无媒体库原样返回")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_backend_registry():
    print("\n[B] 记忆后端插件化")
    tmp = tempfile.mkdtemp()
    try:
        class _DummyBackend(backends.MemoryBackend):
            name = "dummy"

            def initialize(self):
                pass

            def add(self, owner, content, meta=None):
                pass

            def search(self, owner, query, top_k=5):
                return ["dummy-memory"]

            def stats(self, owner=None):
                return {"backend": "dummy", "count": 1, "owner": owner or "*"}

            def clear(self, owner=None):
                return 1

        backends.register_memory_backend("dummy", lambda c, d: _DummyBackend())
        check("dummy" in backends.registered_backends(), "dummy 已注册")

        cfg = types.SimpleNamespace(
            memory=types.SimpleNamespace(backend="dummy"),
            embedding=types.SimpleNamespace(backend="hash", model="x", dim=64,
                                            fallback_hash=True, ollama_base="",
                                            ollama_model=""),
            server=types.SimpleNamespace(data_dir=tmp),
        )
        b = backends.create_memory_backend(cfg, tmp)
        check(b.name == "dummy", f"启用插件后端（{b.name}）")
        check(b.search("u", "q") == ["dummy-memory"], "插件后端 search 生效")

        # 未注册名字 → 回退 builtin
        cfg2 = types.SimpleNamespace(
            memory=types.SimpleNamespace(backend="not-exist"),
            embedding=types.SimpleNamespace(backend="hash", model="x", dim=64,
                                            fallback_hash=True, ollama_base="",
                                            ollama_model=""),
            server=types.SimpleNamespace(data_dir=tmp),
        )
        b2 = backends.create_memory_backend(cfg2, tmp)
        check(b2.name == "builtin", f"未注册名字回退 builtin（{b2.name}）")

        # 插件构造抛错 → 回退 builtin
        def _boom(c, d):
            raise RuntimeError("init failed")

        backends.register_memory_backend("boom", _boom)
        cfg3 = types.SimpleNamespace(
            memory=types.SimpleNamespace(backend="boom"),
            embedding=types.SimpleNamespace(backend="hash", model="x", dim=64,
                                            fallback_hash=True, ollama_base="",
                                            ollama_model=""),
            server=types.SimpleNamespace(data_dir=tmp),
        )
        b3 = backends.create_memory_backend(cfg3, tmp)
        check(b3.name == "builtin", f"插件失败回退 builtin（{b3.name}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_media_tag_extract()
    test_media_tag_publish()
    test_backend_registry()
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")
