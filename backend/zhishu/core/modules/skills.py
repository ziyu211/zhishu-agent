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
            txt = open(p, encoding="utf-8").read().strip()
            if txt:
                out.append(f"【长期记忆 {key}】\n{txt}")
    return out


def build_agent_context_prompt(cfg: ZhishuConfig, owner: str | None = None,
                               is_admin: bool = False,
                               user_role: Optional[str] = None) -> str:
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

    parts.extend(_read_memory_files(cfg, owner))
    return "\n\n".join(p for p in parts if p)


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
# 后台记忆反思（对标 Hermes background_review / 长期记忆沉淀）
# 每轮成功回答后，fork 一次廉价 LLM 调用，从本轮对话中蒸馏出值得长期记住的
# 用户事实/偏好，追加进 MEMORY.md（下一轮自动注入系统提示）。
# 护栏：opt-in（cfg.agent.reflection_enabled，默认关）、阈值校验、全链路异常吞掉
# （反思失败不影响主对话，也不写脏记忆）。
# ===========================================================================
import os as _os
import threading as _threading

_REFLECT_LOCK = _threading.Lock()


async def maybe_reflect(cfg, llm, *, user_message: str, answer: str,
                        owner: str | None = None) -> Optional[str]:
    """从本轮对话蒸馏用户事实，沉淀进长期记忆(MEMORY.md)；返回沉淀要点或 None。"""
    try:
        ac = getattr(cfg, "agent", None)
        if not getattr(ac, "reflection_enabled", False):
            return None
        if not (user_message or "").strip() or len(user_message) < 8:
            return None

        prompt = [
            {"role": "system",
             "content": "你是长期记忆蒸馏器。从用户与助手的对话中，仅提取「值得跨会话记住的、"
                        "关于用户的事实与偏好」，例如：用户角色/职业、技术栈、正在做的项目、"
                        "明确表达过的约定或要求、反复出现的痛点。\n"
                        "输出规则：\n1. 每条事实一行，以「- 」开头，简洁客观；\n"
                        "2. 不要记录一次性任务细节，也不要记录可从当前对话本身推断的临时信息；\n"
                        "3. 若没有明显值得长期记住的内容，只输出空字符串。\n只输出要点，不要解释。"},
            {"role": "user",
             "content": f"用户：{user_message}\n\n助手（节选）：{(answer or '')[:1000]}"},
        ]
        resp = await llm.chat(prompt, model=cfg.default_model)
        body = (resp["choices"][0]["message"].get("content", "") or "").strip()
        facts = [ln.strip().lstrip("-").strip() for ln in body.splitlines()
                 if ln.strip().startswith("-")]
        facts = [f for f in facts if f]
        if not facts:
            return None

        from ...context import get_ctx
        # 安全：反思沉淀写入当前用户自己的记忆目录，不污染他人/全局记忆
        mem_path = _os.path.join(
            user_memory_dir(get_ctx().cfg.server.data_dir, owner), "MEMORY.md")
        new_block = "\n".join(f"- {f}" for f in facts)
        with _REFLECT_LOCK:
            head = ""
            if _os.path.isfile(mem_path):
                try:
                    head = open(mem_path, encoding="utf-8").read().rstrip() + "\n"
                except Exception:
                    head = ""
            with open(mem_path, "w", encoding="utf-8") as f:
                f.write(head + new_block + "\n")

        try:
            get_ctx().audit.log(owner or "system", "memory_reflect",
                                f"沉淀长期记忆 {len(facts)} 条（来源轮次）")
        except Exception:
            pass
        return "; ".join(facts[:5])
    except Exception:
        return None
