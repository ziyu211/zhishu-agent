"""LibreOffice 转换结果缓存回归测试。

验证 ``_libreoffice_convert`` 对相同 ``raw + target_ext`` 只真正启动一次 soffice
（缓存命中），以及不同 target_ext 不共享缓存。无真实 soffice 依赖：
monkeypatch shutil.which / subprocess.run / read_file_text。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zhishu.core.rag as rag


class _FakeProc:
    returncode = 0
    stdout = ""
    stderr = ""


def _make_fake_run(calls):
    def fake_run(cmd, **kw):
        calls["n"] += 1
        outdir = cmd[cmd.index("--outdir") + 1]
        os.makedirs(outdir, exist_ok=True)
        in_name = os.path.basename(cmd[-1])
        with open(os.path.join(outdir, in_name + "x"), "w", encoding="utf-8") as f:
            f.write("")
        return _FakeProc()
    return fake_run


class TestLoConvertCache(unittest.TestCase):
    def setUp(self):
        rag._LO_CONVERT_CACHE.clear()

    def test_same_input_cached(self):
        calls = {"n": 0}
        with patch.object(rag.shutil, "which",
                          lambda x: "soffice" if x in ("soffice", "libreoffice") else None), \
             patch.object(rag, "read_file_text", lambda *a, **k: ("CACHED DOC TEXT", "DOCX")), \
             patch.object(rag.subprocess, "run", _make_fake_run(calls)):
            raw = b"\xd0\xcf\x11\xe0 OLE payload bytes"
            r1 = rag._libreoffice_convert(raw, "report.doc", ".docx")
            r2 = rag._libreoffice_convert(raw, "report.doc", ".docx")
            r3 = rag._libreoffice_convert(raw, "report.doc", ".docx")
        self.assertEqual(r1, ("CACHED DOC TEXT", "DOCX"))
        self.assertEqual(r2, ("CACHED DOC TEXT", "DOCX"))
        self.assertEqual(r3, ("CACHED DOC TEXT", "DOCX"))
        self.assertEqual(calls["n"], 1, "同一输入应只启动一次 soffice（命中缓存）")

    def test_different_target_ext_not_shared(self):
        calls = {"n": 0}
        with patch.object(rag.shutil, "which", lambda x: "soffice"), \
             patch.object(rag, "read_file_text", lambda *a, **k: ("X", "DOCX")), \
             patch.object(rag.subprocess, "run", _make_fake_run(calls)):
            raw = b"same payload bytes"
            rag._libreoffice_convert(raw, "f.doc", ".docx")
            rag._libreoffice_convert(raw, "f.doc", ".xlsx")
        self.assertEqual(calls["n"], 2, "不同 target_ext 应是不同缓存键")


if __name__ == "__main__":
    unittest.main()
