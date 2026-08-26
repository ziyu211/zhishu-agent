"""query_rewrite 检索改写长度上限回归测试。

验证改写结果硬上限为 240 字符（与提示词「Keep it under 240 characters」一致），
超过则被 _normalize_rewrite 拒绝（返回 ""），防止超限问句污染向量召回。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.memory import query_rewrite as qr


class TestQueryRewriteLimit(unittest.TestCase):
    def test_max_constant_is_240(self):
        self.assertEqual(qr._MAX_QUERY_CHARS, 240)

    def test_over_240_rejected(self):
        long_q = "What " + "previous " * 130 + (
            "decision did the user make about the project timeline and budget and scope?")
        self.assertGreater(len(long_q), 240)
        self.assertEqual(qr._normalize_rewrite(long_q), "")

    def test_valid_under_240_accepted(self):
        q = "What previous decisions did the user make about the budget?"
        self.assertLessEqual(len(q), 240)
        out = qr._normalize_rewrite(q)
        self.assertTrue(out.endswith("?"), out)
        self.assertLessEqual(len(out), 240)


if __name__ == "__main__":
    unittest.main()
