"""P1-E 测试：fork 式后台反思（合并反思 + 技能回写，对标 Hermes background_review）。

覆盖：
  1. _reflect_extract_json 稳健解析（裸 JSON / ```json 包裹 / 前后带散文）。
  2. maybe_reflect 合并反思：memory + pitfalls 沉淀进 MEMORY.md（带 [事实]/[约束] 标签），返回 dict。
  3. 技能回写（错峰）：maybe_learn 未触发（skills_auto_learn=False）时，反思发现的技能候选落盘。
  4. 技能回写门控：复杂任务（skills_auto_learn=True 且 tool_total>=min_tools）由 maybe_learn 负责，
     反思不再重复写技能。
  5. 空信号：无内容时返回 None，不写脏记忆。

运行：python tests/test_p1_background_review.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ZHISHU_ALLOW_INSECURE_DEFAULTS", "1")

from zhishu.core.config import ZhishuConfig  # noqa: E402
from zhishu.core.modules import skills as SK  # noqa: E402

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


# ---------------------------------------------------------------------------
# 轻量上下文桩：maybe_reflect 内部会调用 get_ctx().audit.log / cfg.server.data_dir
# ---------------------------------------------------------------------------
class _FakeAudit:
    def log(self, *a, **k):
        pass


class _FakeCtx:
    def __init__(self, cfg):
        self.cfg = cfg
        self.audit = _FakeAudit()


class _FakeLLM:
    """按预设 content 返回一次 chat 结果。"""
    def __init__(self, content):
        self._content = content

    async def chat(self, messages, model=None):
        return {"choices": [{"message": {"content": self._content}}]}


def _fresh_cfg(tmp):
    cfg = ZhishuConfig()
    cfg.server.data_dir = tmp
    SK.get_ctx = lambda: _FakeCtx(cfg)  # 兼容旧引用（无副作用）
    import zhishu.context as C
    C._CTX = _FakeCtx(cfg)
    return cfg


def _mem_path(tmp, owner=None):
    if not owner or owner in ("anonymous", "system"):
        return os.path.join(tmp, "MEMORY.md")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in owner)[:64] or "_"
    return os.path.join(tmp, "memory", safe, "MEMORY.md")


# ---------------------------------------------------------------------------
# 用例 1：JSON 解析容错
# ---------------------------------------------------------------------------
def test_extract_json():
    print("\n[1] _reflect_extract_json 稳健解析")
    plain = '{"memory":["- 事实A"],"pitfalls":[],"skill":null}'
    check(SK._reflect_extract_json(plain) == {"memory": ["- 事实A"], "pitfalls": [], "skill": None},
          "裸 JSON 解析正确")

    fenced = "```json\n" + plain + "\n```"
    d = SK._reflect_extract_json(fenced)
    check(d is not None and d.get("memory") == ["- 事实A"], "```json 包裹可解析")

    prose = "好的，结果是：\n" + plain + "\n以上为提炼。"
    d2 = SK._reflect_extract_json(prose)
    check(d2 is not None and d2.get("skill") is None, "前后带散文仍可截取出 JSON")

    check(SK._reflect_extract_json("不是 JSON 的文本") is None, "非 JSON 返回 None")


# ---------------------------------------------------------------------------
# 用例 2：合并反思沉淀 memory + pitfalls
# ---------------------------------------------------------------------------
async def test_reflect_memory_pitfalls():
    print("\n[2] 合并反思：memory + pitfalls 沉淀进 MEMORY.md")
    tmp = tempfile.mkdtemp()
    try:
        cfg = _fresh_cfg(tmp)
        cfg.agent.reflection_enabled = True
        content = ('{"memory":["- 用户是量化研究员，常用 akshare"],'
                   '"pitfalls":["- 不要在 Windows 上用 os.fork，会崩"],'
                   '"skill":null}')
        llm = _FakeLLM(content)
        res = await SK.maybe_reflect(
            cfg, llm, user_message="帮我用 akshare 拉一下股票数据",
            answer="已用 akshare 拉取并分析。", owner="alice")
        check(isinstance(res, dict), "返回 dict 摘要")
        check(res and any("量化研究员" in m for m in res.get("memory", [])), "memory 已提取")
        check(res and "不要" in res.get("pitfalls", [0])[0], "pitfalls 已提取")
        mp = _mem_path(tmp, "alice")
        check(os.path.isfile(mp), "MEMORY.md 已生成")
        txt = open(mp, encoding="utf-8").read()
        check("[事实] 用户是量化研究员" in txt, "记忆含 [事实] 标签")
        check("[约束] 不要在 Windows" in txt, "记忆含 [约束] 标签")
        check("## 沉淀 ·" in txt, "按日期分节（## 沉淀 · YYYY-MM-DD）")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 用例 3：技能回写（maybe_learn 未触发时）
# ---------------------------------------------------------------------------
async def test_reflect_skill_writeback():
    print("\n[3] 技能回写（skills_auto_learn=False 时由反思补位）")
    tmp = tempfile.mkdtemp()
    try:
        cfg = _fresh_cfg(tmp)
        cfg.agent.reflection_enabled = True
        cfg.agent.skills_auto_learn = False  # 模拟 maybe_learn 不会触发
        content = ('{"memory":[],"pitfalls":[],'
                   '"skill":{"name":"pdf-ocr-fix","description":"PDF 扫描件 OCR 兜底",'
                   '"body":"## 步骤\\n1. 用 soffice 转新格式\\n2. 失败则 Tesseract OCR\\n## 示例\\n..."}}')
        llm = _FakeLLM(content)
        res = await SK.maybe_reflect(
            cfg, llm, user_message="PDF 扫描件怎么提取文字",
            answer="已用 OCR 提取。", owner="bob",
            tool_total=1)
        check(res and res.get("skill") == "pdf-ocr-fix", "技能候选被采纳并命名")
        sk_dir = os.path.join(tmp, "skills", "pdf-ocr-fix")
        check(os.path.isdir(sk_dir), "技能目录已落盘")
        check(os.path.isfile(os.path.join(sk_dir, "SKILL.md")), "SKILL.md 已落盘")
        check(os.path.isfile(os.path.join(sk_dir, "module.json")), "module.json 已落盘")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 用例 4：技能回写门控（复杂任务交给 maybe_learn，反思不重复写）
# ---------------------------------------------------------------------------
async def test_reflect_skill_gated_when_complex():
    print("\n[4] 技能回写门控：复杂任务由 maybe_learn 负责，反思不重复写技能")
    tmp = tempfile.mkdtemp()
    try:
        cfg = _fresh_cfg(tmp)
        cfg.agent.reflection_enabled = True
        cfg.agent.skills_auto_learn = True  # maybe_learn 会触发
        content = ('{"memory":["- 用户在做数据看板"],"pitfalls":[],'
                   '"skill":{"name":"dup-skill","description":"x","body":"## 步骤\\n1. y"}}')
        llm = _FakeLLM(content)
        res = await SK.maybe_reflect(
            cfg, llm, user_message="做个复杂的 ETL 管线并出图",
            answer="已完成。", owner="carol",
            tool_total=10)  # >= skills_auto_learn_min_tools(3) → maybe_learn 已接管
        check(res is not None, "反思仍返回（记忆部分照常）")
        sk_dir = os.path.join(tmp, "skills", "dup-skill")
        check(not os.path.isdir(sk_dir), "复杂任务下反思不写技能（避免与 maybe_learn 重复）")
        mp = _mem_path(tmp, "carol")
        check(os.path.isfile(mp) and "[事实] 用户在做数据看板" in open(mp, encoding="utf-8").read(),
              "记忆沉淀仍正常")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 用例 5：空信号不写脏记忆
# ---------------------------------------------------------------------------
async def test_reflect_empty():
    print("\n[5] 空信号：无内容返回 None 且不写脏记忆")
    tmp = tempfile.mkdtemp()
    try:
        cfg = _fresh_cfg(tmp)
        cfg.agent.reflection_enabled = True
        llm = _FakeLLM('{"memory":[],"pitfalls":[],"skill":null}')
        res = await SK.maybe_reflect(
            cfg, llm, user_message="随便聊聊今天天气",
            answer="今天天气不错。", owner="dave")
        check(res is None, "无信号返回 None")
        check(not os.path.exists(_mem_path(tmp, "dave")), "未生成脏 MEMORY.md")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    test_extract_json()
    loop.run_until_complete(test_reflect_memory_pitfalls())
    loop.run_until_complete(test_reflect_skill_writeback())
    loop.run_until_complete(test_reflect_skill_gated_when_complex())
    loop.run_until_complete(test_reflect_empty())
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
