"""回归测试：技能保存闭环护栏（v1.0.43 · 修复「对话说保存成功、技能页却看不到」）。

覆盖：
  A. agent 回合后校验：_skill_save_intent 命中「保存/创建技能」意图（中英文、含间隔词）；
     _skill_saved_ok 仅认 create_skill 成功（返回含「已持久化」）；
     _guard_skill_save_claim 仅在「有意图且未真实落盘」时追加纠正，不误伤正常回答。
  B. create_tool 硬拦截：_looks_like_skill_save 拦截技能保存意图的误用调用，
     不拦截「生成技能题」等无持久化意图的合法动态工具。
  C. 中文技能名：sanitize_name / skills._sanitize 保留中文（如「写周报」），
     消除「名称滤空→报错→模型改道 create_tool / 谎称成功」的促成路径；
     并经真实 create_skill 工具落盘验证（模块 json + SKILL.md + 本人可见）。

运行：python tests/test_skill_save_guard.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ZHISHU_ALLOW_INSECURE_DEFAULTS"] = "1"

from zhishu.core.agent.agent import (  # noqa: E402
    _skill_save_intent,
    _skill_saved_ok,
    _skill_success_claimed,
    _guard_skill_save_claim,
)
from zhishu.core.tools.builtins.code_exec import (  # noqa: E402
    _looks_like_skill_save,
    _code_writes_skill,
)
from zhishu.core.modules.runtime import sanitize_name  # noqa: E402
from zhishu.core.tools.builtins import skills as skills_tool  # noqa: E402
from zhishu.core.modules.runtime import can_view  # noqa: E402

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


# ---------------- A. 回合后校验 ----------------
def test_intent():
    print("\n[A] 技能保存意图识别 _skill_save_intent")
    pos = [
        "请把这个流程保存为技能",
        "把刚才的步骤做成一个技能",
        "将以上步骤保存成技能",
        "保存技能：周报助手",
        "创建技能，名字叫写周报",
        "把技能保存到技能库",
        "请把上面的方法固化成一个技能",
        "save this as a skill",
        "Please create a skill for weekly reports",
        # 真实复现（v1.0.43 漏判）：动词与「技能」之间夹着技能名 SVIPFTP（9 字符 > 旧版 .{0,6}）
        "创建 SVIPFTP 技能",
        "用户要求创建 SVIPFTP 技能，FTP 信息如下",
    ]
    neg = [
        "技能列表里有什么",
        "如何生成技能",
        "帮我生成技能题",
        "这个技能怎么用",
        "今天天气怎么样",
        "",
        None,
    ]
    for t in pos:
        check(_skill_save_intent(t), f"正例命中: {t!r}")
    for t in neg:
        check(not _skill_save_intent(t), f"负例不命中: {t!r}")


def test_saved_ok():
    print("\n[A] 落盘成功判定 _skill_saved_ok")
    check(_skill_saved_ok([{"name": "create_skill", "result": "[create_skill] 已持久化技能 写周报（磁盘保存…）"}]),
          "create_skill 返回『已持久化』→ 视为成功")
    check(not _skill_saved_ok([{"name": "create_tool", "result": "[create_tool] 已注册工具 dyn_x"}]),
          "create_tool 注册 → 不算技能落盘")
    check(not _skill_saved_ok([{"name": "create_skill", "result": "[create_skill] 技能已存在：x"}]),
          "create_skill 报错 → 不算成功")
    check(not _skill_saved_ok([]), "空轨迹 → 不算成功")
    check(not _skill_saved_ok(None), "None 轨迹 → 不算成功")


def test_claim_guard():
    print("\n[A] 最终回复防假成功 _guard_skill_save_claim")
    ans = "好的，已经帮你保存为技能啦！"
    g1 = _guard_skill_save_claim(ans, "请把这个流程保存为技能", [])
    check("⚠️" in g1 and "已持久化" in g1, "有意图+未落盘 → 追加如实纠正")
    g2 = _guard_skill_save_claim(ans, "请把这个流程保存为技能",
                                 [{"name": "create_skill", "result": "[create_skill] 已持久化技能 写周报"}])
    check("⚠️" not in g2, "有意图+已落盘 → 不追加纠正")
    g3 = _guard_skill_save_claim(ans, "今天天气怎么样", [])
    check("⚠️" not in g3 and g3 == ans, "无意图 → 原样返回")
    g4 = _guard_skill_save_claim("", "保存为技能", [])
    check("⚠️" in g4, "空回答兜底也可追加纠正")


def test_svipftp_real_repro():
    """真实复现（离线内网同版本机器）：用户「创建 SVIPFTP 技能」→ 模型 code_exec 三引号嵌套失败，
    改用 file_write+create_skill 两步法后**谎称「✅ 创建完成」但技能页看不到**。
    验证 v1.0.44 双路护栏：① 动词-名词间隔拉宽后意图命中；② 模型自报成功但轨迹无 create_skill → 纠错；
    ③ code_exec 写技能文件被硬拒（强制改道 create_skill）；④ 引用历史技能不误纠。"""
    print("\n[A] SVIPFTP 真实复现：自报成功假成功 + code_exec 绕道")
    user = "用户要求创建 SVIPFTP 技能，根据记忆上下文，需要确认 FTP 连接信息"
    # 模型最终回复（实为伪造成功，轨迹里没有任何 create_skill 调用）
    answer = ("✅ 创建完成\nSVIPFTP 技能已成功创建！\n\n技能信息\n技能名称 SVIPFTP\n"
              "本次生成的可下载文件（点击即可下载）：svipftp.md")
    check(_skill_save_intent(user), "意图命中：创建 SVIPFTP 技能（间隔 9 字符）")
    check(_skill_success_claimed(answer), "模型自报成功（✅ 创建完成 / 已成功创建）被识别")
    check(not _skill_saved_ok([]), "空轨迹 → 未真实落盘")
    guarded = _guard_skill_save_claim(answer, user, [])
    check("⚠️" in guarded, "双路命中 → 尾部追加如实纠正（假成功不再直达用户）")
    # 真实落盘不应被纠
    ok_traj = [{"name": "create_skill", "result": "[create_skill] 已持久化技能 SVIPFTP 到功能模块技能"}]
    check("⚠️" not in _guard_skill_save_claim("已为您保存 SVIPFTP 技能", user, ok_traj),
          "真实 create_skill 落盘 → 不纠错")
    # 引用历史技能不应误纠
    hist = "你之前成功创建的 SVIPFTP 技能现在可用来上传文件"
    check(not _skill_success_claimed(hist), "引用历史技能（之前创建的）→ 不误判为假成功")
    check("⚠️" not in _guard_skill_save_claim(hist, "怎么用 SVIPFTP 技能", []),
          "引用历史技能 → 不追加纠正")
    # code_exec 绕道硬拒
    bad1 = "import os\nopen('/app/backend/data/skills/SVIPFTP/SKILL.md','w').write(md)"
    bad2 = "with open('svipftp.md','w') as f:\n    f.write('技能保存正文')\n"
    good = "open('result.csv','w',encoding='utf-8').write(csv_text)"
    check(_code_writes_skill(bad1), "code_exec 写 skills/ 目录 → 识别为绕道")
    check(_code_writes_skill(bad2), "code_exec 写『技能』文件 → 识别为绕道")
    check(not _code_writes_skill(good), "code_exec 写普通 csv → 放行")
    # create_tool 误用仍拦截
    check(_looks_like_skill_save("svipftp_skill", "保存 FTP 上传技能"),
          "create_tool 名/描述含技能+保存 → 拦截改道")


# ---------------- B. create_tool 硬拦截 ----------------
def test_create_tool_guard():
    print("\n[B] create_tool 技能保存意图拦截 _looks_like_skill_save")
    check(_looks_like_skill_save("save_skill", "把技能保存到技能库"), "name+desc 含技能保存 → 拦截")
    check(_looks_like_skill_save("skill_saver", "create skill for weekly report"), "英文 save/create skill → 拦截")
    check(not _looks_like_skill_save("quiz_gen", "生成技能题"), "生成技能题（无持久化）→ 放行")
    check(not _looks_like_skill_save("skill_tagger", "为文档打技能标签"), "打技能标签（无保存）→ 放行")
    check(not _looks_like_skill_save("pdf_parser", "解析 PDF 提取表格"), "普通解析工具 → 放行")


# ---------------- C. 中文技能名 + 真实落盘 ----------------
def test_cjk_name_sanitize():
    print("\n[C] 中文技能名 sanitize")
    check(sanitize_name("写周报") == "写周报", "runtime.sanitize_name 保留中文")
    check(sanitize_name("写周报-v2") == "写周报-v2", "中文+ASCII 混合保留")
    _trav = sanitize_name("../etc/passwd")
    check(_trav not in ("", ".", "..") and "/" not in _trav and "\\" not in _trav,
          f"路径穿越字符被过滤（{_trav!r} 非空、非 . / ..、不含 / 或 \\）")
    check(skills_tool._sanitize("报销助手") == "报销助手", "skills._sanitize 保留中文")


def test_create_skill_cjk_disk():
    print("\n[C] create_skill 工具真实落盘（中文名 + 本人可见）")
    d = tempfile.mkdtemp(prefix="zs_skill_")
    real_ctx = None
    try:
        # mock get_ctx（工具内部按需导入），指向临时 data_dir
        from zhishu import context as ctx_mod

        class _Cfg:
            class _Server:
                data_dir = d

            server = _Server()

        class _Ctx:
            cfg = _Cfg()
            user = "tester"
            is_admin = False
            user_role = "user"

        ctx_mod.get_ctx = lambda: _Ctx()
        tool_ctx = types.SimpleNamespace(user="tester", is_admin=False, user_role="user")

        ret = asyncio.run(skills_tool.create_skill(
            {"name": "写周报", "content": "## 写周报\n每周五生成周报模板", "description": "自动生成周报"},
            tool_ctx,
        ))
        check("已持久化" in ret, f"create_skill 返回成功: {ret[:60]}...")

        meta_path = os.path.join(d, "skills", "写周报", "module.json")
        md_path = os.path.join(d, "skills", "写周报", "SKILL.md")
        check(os.path.isfile(meta_path), "module.json 已写入（技能页可见性数据源）")
        check(os.path.isfile(md_path), "SKILL.md 已写入（read_skill 正文）")
        if os.path.isfile(meta_path):
            meta = json.load(open(meta_path, encoding="utf-8"))
            check(meta.get("owner") == "tester", "私有归属当前用户")
            check(meta.get("enabled") is True, "默认启用")
        # 本人可见性（与 _list_modules 同一判定口径）
        check(can_view("tester", "tester", False, False, [], "user"),
              "本人可见（can_view owner 命中）")
        # 重复保存 → 报「已存在」，不覆盖
        ret2 = asyncio.run(skills_tool.create_skill(
            {"name": "写周报", "content": "new content"}, tool_ctx))
        check("已存在" in ret2, "重名技能提示已存在")
    finally:
        ctx_mod.get_ctx = real_ctx if real_ctx is not None else (lambda: None)
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_skill_usage_stat():
    print("\n[C] 技能使用统计 _touch_skill（对标 Hermes skill_usage）")
    d = tempfile.mkdtemp(prefix="zs_skill_usage_")
    try:
        sdir = os.path.join(d, "skills", "写周报")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "module.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "写周报", "content": "x", "enabled": True}, f, ensure_ascii=False)
        skills_tool._touch_skill(d, "写周报")
        meta1 = json.load(open(os.path.join(sdir, "module.json"), encoding="utf-8"))
        check(meta1.get("use_count") == 1 and bool(meta1.get("last_used")),
              "首次 read_skill 后 use_count=1 且记录 last_used")
        skills_tool._touch_skill(d, "写周报")
        skills_tool._touch_skill(d, "写周报")
        meta2 = json.load(open(os.path.join(sdir, "module.json"), encoding="utf-8"))
        check(meta2.get("use_count") == 3, "连续命中 use_count 自增至 3")
        # 不存在的技能不抛异常（失败静默）
        skills_tool._touch_skill(d, "不存在")
        check(True, "不存在的技能 touch 静默无异常")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    print("== 技能保存闭环护栏回归测试 ==")
    test_intent()
    test_saved_ok()
    test_claim_guard()
    test_svipftp_real_repro()
    test_create_tool_guard()
    test_cjk_name_sanitize()
    test_create_skill_cjk_disk()
    test_skill_usage_stat()
    print(f"\n结果：{PASS} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
    sys.exit(1 if FAIL else 0)
