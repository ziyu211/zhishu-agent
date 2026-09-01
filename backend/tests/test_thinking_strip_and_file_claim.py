"""C+D 护栏回归测试（v1.0.57）。

C：思考链泄漏剥离（strip_thinking / ThinkingFilter）
   —— 修复 reasoning 模型（内网 qwen3.5 / sensenova）把 <think>…</think> 思维链混在
   content 里回吐，既泄漏内部推理，又让「我要调用某工具」的意图看上去像已执行动作。

D：「凭空声称已生成文件」幻觉护栏（file_claim_unbacked）
   —— 把 v1.0.55 仅覆盖 code_exec 空结果的护栏扩展到非 code 工具：真实交付必然带
   /media 链接，无链接的「已生成可下载文件」声明必属编造。

用例中的 REAL_TRACE 直接取自内网智枢一次真实故障回复（文档排版任务）。
"""
from zhishu.core.agent.download_guard import (
    strip_thinking,
    ThinkingFilter,
    file_claim_unbacked,
)

# 内网智枢真实故障回复片段：思维链裸吐 + 末尾一个孤立 </think>
REAL_TRACE = (
    "用户要求对 Word 文档进行排版，具体格式要求：\n"
    "字体仿宋 4 号（14pt）\n"
    "我需要先读取文档内容，了解文档结构，然后使用 python-docx 进行排版处理。\n"
    "让我先使用 read_file 工具读取文档内容。<br/></think>\n\n"
    "我来帮您处理这个文档排版任务。首先我需要读取文档内容了解其结构。"
)


# ── C · strip_thinking ────────────────────────────────────────────────
def test_strip_thinking_real_trace_orphan_close():
    """真实故障形态：孤立 </think> 之前全是思考链，须整段丢弃。"""
    out = strip_thinking(REAL_TRACE)
    assert out == "我来帮您处理这个文档排版任务。首先我需要读取文档内容了解其结构。"
    assert "</think>" not in out
    assert "<br/>" not in out
    assert "用户要求对 Word 文档进行排版" not in out


def test_strip_thinking_paired_tags():
    assert strip_thinking("<think>内部推理不该外泄</think>正式回复") == "正式回复"
    assert strip_thinking("<thinking>abc</thinking>结论") == "结论"
    assert strip_thinking("<reasoning>x</reasoning>Y") == "Y"


def test_strip_thinking_paired_tags_midtext():
    out = strip_thinking("前言\n<think>隐藏</think>\n后文")
    assert "隐藏" not in out
    assert "前言" in out and "后文" in out


def test_strip_thinking_orphan_open():
    """孤立开始标签：其后全部为思考链。"""
    out = strip_thinking("这是给用户的回复。\n<think>接下来我要偷偷想…")
    assert out == "这是给用户的回复。"


def test_strip_thinking_never_empties_answer():
    """全篇皆思考链时，退化为仅去标签，绝不把回复清成空白。"""
    out = strip_thinking("<think>只有思考没有正文")
    assert out.strip() != ""
    assert "只有思考没有正文" in out


def test_strip_thinking_tag_variants_and_case():
    assert strip_thinking("< THINK >x</ think >Y").strip() == "Y"
    assert strip_thinking("abc</THINK>def") == "def"


def test_strip_thinking_noop_on_clean_text():
    clean = "这是一段正常回复，包含 /media/generated/a.docx 链接。"
    assert strip_thinking(clean) == clean


def test_strip_thinking_empty_input():
    assert strip_thinking("") == ""
    assert strip_thinking(None) == ""


# ── C · ThinkingFilter（流式）──────────────────────────────────────────
def test_thinking_filter_paired_across_chunks():
    """标签被切分在多个 chunk 中也须正确抑制。"""
    f = ThinkingFilter()
    got = "".join([f.feed("hello "), f.feed("<thi"), f.feed("nk>secret"),
                   f.feed(" more</thi"), f.feed("nk>world")])
    got += f.flush()
    assert got == "hello world"
    assert "secret" not in got


def test_thinking_filter_orphan_close_drops_tag_only():
    f = ThinkingFilter()
    got = f.feed("abc</think>def") + f.flush()
    assert got == "abcdef"


def test_thinking_filter_buffers_partial_tag_tail():
    f = ThinkingFilter()
    first = f.feed("hello<")
    assert first == "hello"          # 半截标签暂存，不外发
    assert f.flush() == "<"          # 收尾确认不是标签 → 补回


def test_thinking_filter_noop_stream():
    f = ThinkingFilter()
    out = "".join(f.feed(c) for c in ["正常", "流式", "输出"]) + f.flush()
    assert out == "正常流式输出"


# ── D · file_claim_unbacked ───────────────────────────────────────────
FAKE_DELIVERY = "已完成排版。\n📎 **本次生成的可下载文件（点击即可下载）：**\n元.docx"


def test_file_claim_unbacked_detects_fabricated_delivery():
    """真实故障形态：声称生成可下载文件，却无任何 /media 链接。"""
    assert file_claim_unbacked(FAKE_DELIVERY, []) is True


def test_file_claim_ok_when_answer_has_media_link():
    ok = ("已完成排版。\n📎 本次生成的可下载文件：\n"
          "- [元.docx](/media/generated/admin/元.docx)")
    assert file_claim_unbacked(ok, []) is False


def test_file_claim_ok_when_tool_produced_link():
    """回复漏贴链接但工具确实产出过 → 交由下载护栏补链接，本护栏不误报。"""
    assert file_claim_unbacked(
        FAKE_DELIVERY, ["已生成 /media/generated/admin/元.docx"]) is False


def test_file_claim_variants_detected():
    for txt in [
        "已为您生成排版后的文档",
        "文件已生成",
        "排版完成",
        "已导出 xlsx 表格",
        "点击下载",
    ]:
        assert file_claim_unbacked(txt, []) is True, txt


def test_file_claim_no_false_positive_on_analysis():
    for txt in [
        "这是对文档结构的分析结论：共 12 段，其中落款 1 段。",
        "我无法读取该文件，请确认路径后重新提供。",
        "排版规则说明：仿宋 14pt，首行缩进 2 字符。",
        "",
    ]:
        assert file_claim_unbacked(txt, []) is False, txt
