"""智枢智能体 —— 技能层（对标 Hermes `agent/skill_utils.py`）。

技能以「目录即模块」管理：data/skills/<name>/module.json（元信息）+ SKILL.md（指令正文）。
默认（skills_progressive=False）把全部已启用技能的完整指令注入系统提示 volatile 部分，
与重构前行为一致；开启渐进披露（opt-in，cfg.agent.skills_progressive）后，仅注入「技能
清单」（name: description），模型按需调用 read_skill 工具读取 SKILL.md 全文，避免上下文膨胀。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from ..config import ZhishuConfig


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "", (name or "").strip())[:64]


def _skills_root() -> str:
    from ...context import get_ctx
    return os.path.join(get_ctx().cfg.server.data_dir, "skills")


def _read_skill_content(name: str) -> str:
    """读取技能正文：优先 SKILL.md，回退 module.json / skill.json 的 content 字段。"""
    d = os.path.join(_skills_root(), _sanitize(name))
    for fn in ("SKILL.md", "module.json", "skill.json"):
        fp = os.path.join(d, fn)
        if os.path.isfile(fp):
            try:
                if fn.endswith(".md"):
                    return open(fp, encoding="utf-8").read().strip()
                return (json.load(open(fp, encoding="utf-8")).get("content") or "").strip()
            except Exception:
                continue
    return ""


def _enabled_skills(cfg: Optional[ZhishuConfig] = None,
                    username: Optional[str] = None,
                    is_admin: bool = False,
                    user_role: Optional[str] = None) -> list[dict]:
    """返回已启用技能的 [{"name","description","content"}] 列表。

    多用户隔离：仅返回「共享（无 owner）+ 本人 + 角色命中」技能；admin 全量。
    防止把 A 用户的私有技能正文注入 B 用户的系统提示（泄露面）。"""
    from .runtime import load_state, read_meta, DISABLED_KEY, can_view

    try:
        if cfg is None:
            from ...context import get_ctx
            cfg = get_ctx().cfg
    except Exception:
        return []
    root = os.path.join(cfg.server.data_dir, "skills")
    if not os.path.isdir(root):
        return []
    state = load_state()
    disabled = set(state.get(DISABLED_KEY["skills"], []))
    out: list[dict] = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d) or name in disabled:
            continue
        meta = read_meta("skills", name)
        if meta.get("enabled") is False:
            continue
        if not can_view(meta.get("owner") or None, username, is_admin,
                        bool(meta.get("shared")), meta.get("share_with") or None, user_role):
            continue
        out.append({
            "name": name,
            "description": meta.get("description", ""),
            "content": (meta.get("content") or "") or _read_skill_content(name),
        })
    return out


def user_memory_dir(data_dir: str, owner: str | None) -> str:
    """按用户隔离的长期记忆目录（安全）。

    - 有明确用户（多用户鉴权开启）→ ``data_dir/memory/{owner}/``，
      每个用户独立的 MEMORY.md / USER.md / SOUL.md，互不可见。
    - 无用户上下文（enable_auth=False 的 anonymous / system 后台任务）→
      退回 ``data_dir`` 根（历史全局文件，单用户部署行为不变）。
    owner 会被清洗为安全目录名，防止路径穿越。
    """
    if not owner or owner in ("anonymous", "system"):
        return data_dir
    import re as _re
    safe = _re.sub(r"[^\w.\-]", "_", str(owner))[:64] or "_"
    d = os.path.join(data_dir, "memory", safe)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return data_dir
    return d


def _read_memory_files(cfg: ZhishuConfig, owner: str | None = None) -> list[str]:
    out = []
    base = user_memory_dir(cfg.server.data_dir, owner)
    for key, fn in (("memory", "MEMORY.md"), ("user", "USER.md"), ("soul", "SOUL.md")):
        p = os.path.join(base, fn)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                txt = fh.read().strip()
            if txt:
                out.append(f"【长期记忆 {key}】\n{txt}")
    return out


def build_agent_context_prompt(cfg: ZhishuConfig, owner: str | None = None,
                               is_admin: bool = False,
                               user_role: Optional[str] = None,
                               *, include_memory: bool = True) -> str:
    """组装注入系统提示的 volatile 部分：已启用技能 + 长期记忆文件。

    开启 cfg.agent.skills_progressive 时改为「技能清单」模式（渐进披露）。
    无技能/记忆时返回空字符串（与重构前行为一致，不污染系统提示）。
    长期记忆按 owner 隔离（见 user_memory_dir）；技能按 owner+is_admin+角色 过滤，
    防止跨用户记忆/技能泄露。
    """
    parts: list[str] = []

    progressive = getattr(getattr(cfg, "agent", None), "skills_progressive", False)
    skills = _enabled_skills(cfg, username=owner, is_admin=is_admin, user_role=user_role)
    if skills:
        if progressive:
            lines = ["可用技能（调用 read_skill 工具并按需读取其完整指令）："]
            for s in skills:
                lines.append(f"- {s['name']}: {s.get('description', '')}")
            parts.append("【已启用技能 Skills】\n" + "\n".join(lines))
        else:
            blocks = [f"## {s['name']}\n{s.get('content', '')}" for s in skills if (s.get('content') or '').strip()]
            if blocks:
                parts.append("【已启用技能 Skills】\n" + "\n\n".join(blocks))

    if include_memory:
        parts.extend(_read_memory_files(cfg, owner))
    return "\n\n".join(p for p in parts if p)


def build_user_memory_prompt(cfg: ZhishuConfig, owner: str | None = None) -> str:
    """仅取用户长期记忆（MEMORY/USER/SOUL.md），不含技能指令。

    用于「子智能体继承用户记忆」：子智能体应保持专属人设、不被全局技能清单干扰，
    但仍需感知用户画像/约定/踩坑经验，避免重复询问或违背既有偏好。按 owner 隔离。
    """
    return "\n\n".join(p for p in _read_memory_files(cfg, owner) if p)


# ===========================================================================
# 技能自进化闭环（对标 Hermes learning loop）
# 复杂任务（步数/工具数达标）且成功完成后，由 LLM 把「原始请求 + 工具调用轨迹 + 最终答案」
# 蒸馏为一份可复用的 SKILL.md，落盘到技能目录并审计。下次同类任务即可被直接复用。
# 护栏：阈值校验 + 重名跳过 + 全链路异常吞掉（自进化失败不影响主对话）。
# ===========================================================================
async def maybe_learn(cfg, llm, *, user_message: str, answer: str,
                      traj_tools: list[dict], steps_used: int,
                      tool_total: int, owner: str | None = None) -> Optional[str]:
    """复杂任务完成后自动沉淀技能；返回新建技能名或 None。"""
    try:
        ac = getattr(cfg, "agent", None)
        if not getattr(ac, "skills_auto_learn", False):
            return None
        if steps_used < getattr(ac, "skills_auto_learn_min_steps", 8):
            return None
        if tool_total < getattr(ac, "skills_auto_learn_min_tools", 3):
            return None
        if not (user_message or "").strip() or len(user_message) < 8:
            return None

        slug = _skill_slug(user_message)
        root = os.path.join(cfg.server.data_dir, "skills")
        target = os.path.join(root, slug)
        if os.path.isdir(target):        # 同名技能已存在，跳过（避免重复堆积）
            return None
        os.makedirs(target, exist_ok=True)

        traj_text = "\n".join(
            f"- 工具 {t.get('name')} 入参={json.dumps(t.get('args', {}), ensure_ascii=False)[:300]} "
            f"→ 结果摘要：{(t.get('result') or '')[:200]}"
            for t in traj_tools
        )
        prompt = [
            {"role": "system",
             "content": "你是技能蒸馏器。根据用户原始请求、使用的工具轨迹与最终答案，"
                        "生成一份可复用的技能文档（Markdown）。要求：\n"
                        "1. 顶部用一行写 ## 标题（简短技能名）；\n"
                        "2. 一段 description（何时使用该技能）；\n"
                        "3. ## 步骤：分条列出可复用的操作步骤，含具体工具名与关键参数；\n"
                        "4. ## 示例：一个调用示例。\n"
                        "只输出技能正文，不要额外解释。"},
            {"role": "user",
             "content": f"原始请求：{user_message}\n\n工具轨迹：\n{traj_text}\n\n"
                        f"最终答案（节选）：{(answer or '')[:800]}"},
        ]
        resp = await llm.chat(prompt, model=cfg.default_model)
        body = resp["choices"][0]["message"].get("content", "").strip()
        if not body:
            return None

        # 元信息（module.json）供 modules API 读取/开关。
        # 多用户隔离：自动沉淀的技能归属触发它的用户（私有）；
        # 匿名/后台任务（anonymous/system）不写 owner，保持系统级共享。
        meta = {
            "name": slug,
            "description": (body.split("\n", 1)[0].lstrip("#").strip()
                            or f"自动沉淀：{user_message[:40]}"),
            "enabled": True,
            "auto_generated": True,
            "created_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            "source_task": user_message[:200],
        }
        if owner and owner not in ("anonymous", "system"):
            meta["owner"] = owner
        with open(os.path.join(target, "module.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        with open(os.path.join(target, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(body)

        # 审计：记录自进化动作（detail 经脱敏）
        try:
            from ...context import get_ctx
            get_ctx().audit.log(owner or "system", "skill_auto_learn",
                                f"沉淀技能 {slug}（步数={steps_used}, 工具={tool_total}）")
        except Exception:
            pass
        return slug
    except Exception:
        return None


def _skill_slug(text: str) -> str:
    """从请求文本生成稳定技能目录名（去停用词 + 截断）。"""
    import re as _re
    stop = set("的 了 我 你 他 她 它 我们 你们 他们 一个 进行 使用 如何 怎么 怎样 请 帮 我 把"
               "在 是 和 与 对 为 用 这 那 该 这个 那个".split())
    words = [w for w in _re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_]+", (text or "")) if w not in stop]
    slug = "_".join(words[:6]).lower()
    slug = _sanitize(slug) or "task"
    return f"auto_{slug}"[:64]


# ===========================================================================
# 后台记忆反思（对标 Hermes background_review / 长期记忆沉淀 —— 并超越）
# 每轮成功回答后，以 asyncio.create_task 非阻塞触发（等价 Hermes 的「后台线程 fork」，
# 协程式实现，不额外占 OS 线程、不碰主会话 prompt cache）。
# 单次 LLM 调用做「合并反思」：从本轮对话（用户请求 + 助手回答 + 工具轨迹）中一次性提炼
#   ① memory   —— 跨会话该记的用户事实/偏好
#   ② pitfalls —— 踩坑/约束/环境约定（写入记忆防未来复犯；Hermes 把这类塞进 skill，
#                  智枢统一沉淀进 MEMORY.md 更干净，避免 skill 被环境性负向声明污染）
#   ③ skill    —— 可复用技能候选（命中阈值则落盘，与 maybe_learn 错峰避免重复）
# 护栏：opt-in（reflection_enabled 默认开）、阈值校验、JSON 容错解析、全链路异常吞掉
# （反思失败不影响主对话，也不写脏记忆）。
# ===========================================================================
import os as _os
import threading as _threading

_REFLECT_LOCK = _threading.Lock()

_REFLECT_REVIEW_SYSTEM_PROMPT = (
    "你是长期记忆与技能蒸馏器。从本轮对话（用户请求 + 助手回答 + 工具轨迹）中，"
    "提炼「跨会话应保留」的积累。\n"
    "必须只输出一个 JSON 对象，不要任何额外文字、不要 Markdown 代码块包裹，格式严格如下：\n"
    "{\n"
    '  "memory": ["关于用户的事实/偏好，每条以「- 」开头，简洁客观，约 20 字"],\n'
    '  "pitfalls": ["踩坑/约束/环境约定，未来应避免或注意；每条以「- 」开头，约 20 字。'
    "不要写一次性环境故障，只写可复用的教训\"],\n"
    '  "skill": null\n'
    "}\n"
    "skill 字段：当存在明确、可复用的方法论/技巧/修复方案时，改为：\n"
    '{"name":"kebab-slug","description":"何时使用","body":"## 步骤\\n1. ...\\n## 示例\\n..."}'
    "；否则保持 null。\n"
    "规则：\n"
    "1. memory 记「用户是谁、想要什么」；pitfalls 记「怎么做会踩坑、环境有什么约定」；skill 记「可复制的做法」。\n"
    "2. 若某维度无明显内容，给空数组 [] 或 null。\n"
    "3. 不要记录一次性任务细节、可从当前对话推断的临时信息。\n"
    "4. skill.name 小写 kebab，不超过 40 字符；body 用 Markdown，含简短步骤与示例。"
)


def _reflect_extract_json(text: str):
    """从 LLM 文本中稳健解析反思 JSON（容错：去 ```json 包裹、截取首个 { 到末个 }）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t[3:]
        if "```" in t:
            t = t.split("```", 1)[0]
        t = t.strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e == -1 or e <= s:
        return None
    try:
        return json.loads(t[s:e + 1])
    except Exception:
        return None


def _persist_skill_file(cfg, slug: str, body: str, owner: str | None,
                        source_task: str, description: str | None = None) -> bool:
    """把技能正文落盘到 data_dir/skills/<slug>/{module.json,SKILL.md}；已存在则跳过。"""
    try:
        root = os.path.join(cfg.server.data_dir, "skills")
        target = os.path.join(root, _sanitize(slug))
        if os.path.isdir(target):
            return False
        os.makedirs(target, exist_ok=True)
        meta = {
            "name": slug,
            "description": (description or body.split("\n", 1)[0].lstrip("#").strip()
                            or f"自动沉淀：{source_task[:40]}"),
            "enabled": True,
            "auto_generated": True,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_task": source_task[:200],
        }
        if owner and owner not in ("anonymous", "system"):
            meta["owner"] = owner
        with open(os.path.join(target, "module.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        with open(os.path.join(target, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(body)
        return True
    except Exception:
        return False


async def maybe_reflect(cfg, llm, *, user_message: str, answer: str,
                        owner: str | None = None,
                        traj_tools: list[dict] | None = None,
                        steps_used: int = 0,
                        tool_total: int = 0) -> Optional[dict]:
    """完整后台反思（对标 Hermes background_review 的 combined review + 技能回写）。

    单 LLM 调用产出结构化 JSON（memory / pitfalls / skill），沉淀进长期记忆(MEMORY.md)，
    并在命中阈值时把可复用技能落盘。以 create_task 非阻塞触发，全链路异常安全。
    返回 {\"memory\":[...], \"pitfalls\":[...], \"skill\":name|None} 便于测试/审计。
    """
    try:
        ac = getattr(cfg, "agent", None)
        if not getattr(ac, "reflection_enabled", False):
            return None
        if not (user_message or "").strip() or len(user_message) < 8:
            return None

        traj_text = ""
        if traj_tools:
            traj_text = "\n".join(
                f"- 工具 {t.get('name')} 入参={json.dumps(t.get('args', {}), ensure_ascii=False)[:200]} "
                f"→ 结果摘要：{(t.get('result') or '')[:120]}"
                for t in traj_tools[:24]
            )

        prompt = [
            {"role": "system", "content": _REFLECT_REVIEW_SYSTEM_PROMPT},
            {"role": "user",
             "content": f"用户请求：{user_message}\n\n助手回答（节选）：{(answer or '')[:1200]}\n\n"
                        f"工具轨迹（节选）：\n{traj_text or '（无）'}"},
        ]
        resp = await llm.chat(prompt, model=cfg.default_model)
        body_text = (resp["choices"][0]["message"].get("content", "") or "").strip()
        data = _reflect_extract_json(body_text)
        if not isinstance(data, dict):
            return None

        memory = [str(x).strip().lstrip("-").strip() for x in (data.get("memory") or []) if str(x).strip()]
        pitfalls = [str(x).strip().lstrip("-").strip() for x in (data.get("pitfalls") or []) if str(x).strip()]
        if not memory and not pitfalls and not data.get("skill"):
            return None

        from ...context import get_ctx
        ctx = get_ctx()
        # 安全：反思沉淀写入当前用户自己的记忆目录，不污染他人/全局记忆
        mem_path = _os.path.join(
            user_memory_dir(ctx.cfg.server.data_dir, owner), "MEMORY.md")

        # ---- 记忆沉淀（带日期分节 + [事实]/[约束] 标签，便于后续检索/蒸馏）----
        written: list[str] = []
        if memory or pitfalls:
            day = time.strftime("%Y-%m-%d")
            lines = [f"---\n## 沉淀 · {day}"]
            for f in memory:
                lines.append(f"- [事实] {f}")
            for p in pitfalls:
                lines.append(f"- [约束] {p}")
            new_block = "\n".join(lines) + "\n"
            with _REFLECT_LOCK:
                head = ""
                if _os.path.isfile(mem_path):
                    try:
                        head = open(mem_path, encoding="utf-8").read().rstrip() + "\n"
                    except Exception:
                        head = ""
                with open(mem_path, "w", encoding="utf-8") as f:
                    f.write(head + new_block)
            written = memory + pitfalls

        # ---- 技能回写（与 maybe_learn 错峰：复杂任务已由 maybe_learn 写，
        #      这里补「简单但可复用」的技巧，避免重复沉淀）----
        skill_name: str | None = None
        sk = data.get("skill")
        if isinstance(sk, dict) and sk.get("name") and sk.get("body"):
            ac2 = getattr(cfg, "agent", None)
            maybe_learn_fired = (getattr(ac2, "skills_auto_learn", False)
                                 and tool_total >= getattr(ac2, "skills_auto_learn_min_tools", 3))
            if not maybe_learn_fired:
                raw = re.sub(r"[^A-Za-z0-9_\-]", "-", str(sk.get("name")).strip().lower())
                slug = _sanitize(raw)[:40] or "task"
                if _persist_skill_file(cfg, slug, str(sk.get("body")), owner,
                                       user_message, description=str(sk.get("description") or "")):
                    skill_name = slug

        try:
            ctx.audit.log(owner or "system", "memory_reflect",
                          f"沉淀记忆 {len(written)} 条"
                          + (f" + 技能 {skill_name}" if skill_name else ""))
        except Exception:
            pass
        return {"memory": memory, "pitfalls": pitfalls, "skill": skill_name}
    except Exception:
        return None
