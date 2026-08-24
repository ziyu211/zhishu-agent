"""P1 记忆/视觉增强回归测试（v1.0.38）。

覆盖：
  A. 记忆质量演化层（quality_store）：
       - record 登记 + 重复更新不清零；
       - feedback 调整 trust（helpful +0.05 / unhelpful −0.10，含边界钳制）；
       - rank 按 trust 优先 + 时效衰减重排；
       - contradict 矛盾检测（高相似不同文本判冲突，低相似不判）；
       - stats / clear。
  B. vector_provider 工具：memory_feedback schema + handle_tool_call 调 quality。
  C. aux 视觉回退：find_vision_provider 探测、format_aux_descriptions 文本块。

运行：python tests/test_p1_quality_aux_vision.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.memory.quality_store import (  # noqa: E402
    MemoryQualityStore, TRUST_DELTA_HELPFUL, TRUST_DELTA_UNHELPFUL,
    TRUST_MAX, TRUST_MIN,
)
from zhishu.core import image_routing  # noqa: E402

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


def test_quality_record_and_feedback():
    print("\n[A1] 质量登记与反馈训练")
    tmp = tempfile.mkdtemp()
    try:
        q = MemoryQualityStore(os.path.join(tmp, "q.db"))
        q.record("alice", "用户偏好国产大模型用于内网部署")
        q.record("alice", "用户在做内网大模型部署")
        # 重复登记同一内容：不清零 trust（幂等）
        q.record("alice", "用户偏好国产大模型用于内网部署")
        s = q.stats("alice")
        check(s["count"] == 2, f"登记 2 条去重（count={s['count']}）")

        # helpful +0.05
        t1 = q.feedback("alice", "用户偏好国产大模型用于内网部署", helpful=True)
        check(t1 is not None and abs(t1 - TRUST_DELTA_HELPFUL) < 1e-6,
              f"helpful 反馈 trust={t1}（应 +0.05）")
        # unhelpful −0.10
        t2 = q.feedback("alice", "用户偏好国产大模型用于内网部署", helpful=False)
        check(t2 is not None and abs(t2 - (TRUST_DELTA_HELPFUL + TRUST_DELTA_UNHELPFUL)) < 1e-6,
              f"unhelpful 反馈 trust={t2}（应 −0.05）")
        # 未知内容 → None
        t3 = q.feedback("alice", "完全不存在的记忆内容xyz", helpful=True)
        check(t3 is None, "未知记忆反馈返回 None")
        # 钳制到 [MIN, MAX]
        for _ in range(50):
            q.feedback("alice", "用户偏好国产大模型用于内网部署", helpful=True)
        t4 = q.feedback("alice", "用户偏好国产大模型用于内网部署", helpful=True)
        check(t4 is not None and t4 <= TRUST_MAX + 1e-6, f"trust 不超上限（{t4}）")
        q.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_rank_decay():
    print("\n[A2] 检索重排：trust 优先 + 时效衰减")
    tmp = tempfile.mkdtemp()
    try:
        q = MemoryQualityStore(os.path.join(tmp, "q.db"))
        old = "很久以前的记忆内容AAA"
        new = "最近的记忆内容BBB"
        q.record("bob", old)
        q.record("bob", new)
        # 旧记忆获得高分，新记忆低分 → trust 应压过时效
        for _ in range(30):
            q.feedback("bob", old, helpful=True)
        ranked = q.rank("bob", [new, old], top_k=2)
        check(ranked[0] == old, f"高 trust 旧记忆排前（ranked={ranked}）")
        # 无质量记录的中性项保持在末尾
        ranked2 = q.rank("carol", ["未知记忆X", "未知记忆Y"], top_k=1)
        check(len(ranked2) == 1, "无记录记忆按原始顺序取 top_k")
        q.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_contradict():
    print("\n[A3] 矛盾检测")
    tmp = tempfile.mkdtemp()
    try:
        q = MemoryQualityStore(os.path.join(tmp, "q.db"))
        old = "用户公司名称是明垚科技"
        # 高相似但内容不同 → 判冲突
        c1 = q.contradict("alice", "用户公司名称是启创科技", [old])
        check(c1 == [old], f"高相似不同内容判冲突（{c1}）")
        # 完全无关 → 不判
        c2 = q.contradict("alice", "用户喜欢喝美式咖啡", [old])
        check(c2 == [], f"无关内容不判冲突（{c2}）")
        # 完全一致 → 不算矛盾（避免自我冲突）
        c3 = q.contradict("alice", old, [old])
        check(c3 == [], "完全相同不算矛盾")
        # 过短 → 不判
        c4 = q.contradict("alice", "好", [old])
        check(c4 == [], "过短输入不判")
        q.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_vector_provider_feedback_tool():
    print("\n[B] vector provider memory_feedback 工具")
    tmp = tempfile.mkdtemp()
    try:
        from types import SimpleNamespace
        from zhishu.core.memory.vector_provider import VectorMemoryProvider, FEEDBACK_TOOL

        cfg = SimpleNamespace(
            memory=SimpleNamespace(backend="builtin", vector_enabled=True,
                                   vector_top_k=5, query_rewrite_enabled=False,
                                   extraction_enabled=False, extraction_interval=6,
                                   extraction_model=None),
            embedding=SimpleNamespace(backend="hash", model="x", dim=64,
                                      fallback_hash=True, ollama_base="", ollama_model=""),
            server=SimpleNamespace(data_dir=tmp),
        )
        # 用 hash embedding 规避真实模型依赖
        cfg.embedding.backend = "hash"
        p = VectorMemoryProvider(cfg, tmp, top_k=2)
        try:
            schemas = p.get_tool_schemas()
            check(any(s["name"] == FEEDBACK_TOOL for s in schemas),
                  f"暴露 {FEEDBACK_TOOL} 工具")
            # 先登记一条，反馈后应返回 ok
            p.quality.record("u1", "测试记忆内容")
            r = p.handle_tool_call(FEEDBACK_TOOL,
                                   {"content": "测试记忆内容", "helpful": True},
                                   owner="u1")
            check('"ok": true' in r, f"反馈成功（{r}）")
            r2 = p.handle_tool_call(FEEDBACK_TOOL,
                                    {"content": "不存在的记忆", "helpful": True},
                                    owner="u1")
            check('"ok": false' in r2, f"未知记忆反馈失败（{r2}）")
        finally:
            try:
                p.shutdown()
            except Exception:
                pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_aux_vision_helpers():
    print("\n[C] aux 视觉回退辅助")
    tmp = tempfile.mkdtemp()
    try:
        from types import SimpleNamespace
        from zhishu.core.config import ProviderConfig

        # 有视觉模型 → 探测命中
        cfg = SimpleNamespace()
        vis = SimpleNamespace(name="vis", enabled=True, api_key="k",
                              base_url="https://api.example.com",
                              models=["qwen2.5-vl-7b", "qwen2.5-7b"])
        no_vis = SimpleNamespace(name="plain", enabled=True, api_key="k",
                                 base_url="https://api.example.com",
                                 models=["qwen2.5-7b"])
        cfg.ordered_providers = lambda: [no_vis, vis]
        pc, mdl = image_routing.find_vision_provider(cfg)
        check(mdl == "qwen2.5-vl-7b", f"探测到视觉模型（{mdl}）")

        # 无视觉模型 → (None, None)
        cfg2 = SimpleNamespace()
        cfg2.ordered_providers = lambda: [no_vis]
        pc2, mdl2 = image_routing.find_vision_provider(cfg2)
        check(pc2 is None and mdl2 is None, "无视觉模型返回 (None, None)")

        # 描述文本块格式
        block = image_routing.format_aux_descriptions(["图1：一张表格截图，含销售数据"])
        check("图片内容" in block and "图1" in block, "描述文本块格式正确")
        check(image_routing.format_aux_descriptions([]) == "", "空描述返回空串")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_quality_record_and_feedback()
    test_quality_rank_decay()
    test_quality_contradict()
    test_vector_provider_feedback_tool()
    test_aux_vision_helpers()
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")
