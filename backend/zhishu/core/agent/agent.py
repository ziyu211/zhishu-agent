"""智枢智能体 —— Agent 核心循环（ReAct + 流式输出）。

  流程：
    1. 组装上下文：系统提示（分层：stable 身份 + volatile 技能/记忆/知识库）
       + 会话记忆（可选经 ContextEngine 压缩）+ （可选）RAG 检索增强。
    2. 调用 LLM 推理（支持 moa 多智能体 facade / 国产 Provider 回退链）。
    3. 若返回 tool_calls → 经工具注册中心执行（沙箱 + 出网隔离 + 审计）→ 回填 → 循环。
    4. 若为最终回答 → 流式返回 token，结束。
    5. 全过程通过异步生成器 yield 结构化事件，供 SSE 推送。

  本文件对应 Hermes 的 `AIAgent` + `run_conversation()`；把「系统提示组装」「上下文
  压缩」「多智能体」下沉到同包下的 system_prompt / context_engine / moa 模块。
"""
from __future__ import annotations

import asyncio
import ast
import contextlib
import json
import os
import re
import time
from typing import AsyncIterator, Optional

from ..providers.client import LLMClient
from ..tools import ToolRegistry, ToolContext, set_current_user
from ..rag import KnowledgeBase
from ..memory import MemoryStore, MemoryManager
from ..modules import build_agent_context_prompt
from ..agents_runtime import (
    DELEGATE_TOOL_NAME,
    agent_owner,
    get_agent_meta,
    is_enabled,
    resolve_tools,
    build_agent_system_prompt,
)
from ..modules.runtime import can_view, filter_tool_specs
from ..config import ZhishuConfig, classify_model
from .system_prompt import build_system_prompt
from .download_guard import (
    guard_download_links,
    extract_media_links,
    find_leaked_paths,
    strip_evasion,
    strip_leaked_paths,
)
from .context_engine import NoOpContextEngine, ContextEngine, CompressionContextEngine
from ..modules.skills import maybe_learn, maybe_reflect
from .. import image_routing

MAX_STEPS = 90  # 默认迭代预算（对齐 Hermes 父 Agent）；run() 优先用 cfg.agent.max_steps

# ---------------------------------------------------------------------------
# 文本委派解析（弱模型兼容层）
#
# 背景：部分国产模型（尤其 flash / 小参数量档）在被要求「调用 delegate_to_agent」时
# 不会真正发起 function call，而是把调用写成普通文本或 Markdown 代码块，例如：
#     ```python
#     delegate_to_agent(agent_name="Research", task="分析新能源车行业格局")
#     ```
# 若不处理，主管 Agent 就只是「说了要委派」，子智能体从未被真正执行，多 Agent
# 协作在前端表现为「一句话就 done」。这里把这类文本调用解析出来，走与 function
# call 完全相同的 _run_delegate 路径，让弱模型也能跑通 Supervisor→SubAgent。
# ---------------------------------------------------------------------------
_DELEGATE_CALL_RE = re.compile(r"\bdelegate_to_agent\s*\(")
# 部分国产模型（如 sensenova/glm-5.2）不输出标准 function_call，而是输出 XML 标签形式的
# 工具调用。常见格式：
#   1) <tool_call>delegate_to_agent agent_name="Research" task="..."</tool_call>
#   2) <tool_call>delegate_to_agent(agent_name="Research", task="...")</tool_call>
#   3) <delegate_to_agent><agent_name>Research</agent_name><task>...</task></delegate_to_agent>
# 需要先把这些标签转换为普通函数调用格式，再走原有解析路径。
_DELEGATE_XML_RE = re.compile(
    r"<tool_call>\s*delegate_to_agent\s+(.*?)</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_DELEGATE_XML_PAREN_RE = re.compile(
    r"<tool_call>\s*(delegate_to_agent\s*\(.*?)\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_DELEGATE_XML_BLOCK_RE = re.compile(
    r"<delegate_to_agent>(.*?)</delegate_to_agent>",
    re.DOTALL | re.IGNORECASE,
)
_DELEGATE_XML_TAG_RE = re.compile(
    r"<(%s)>(.*?)</\1>" % "|".join(("agent_name", "agent", "name", "sub_agent",
                                      "target", "task", "instruction", "prompt",
                                      "input", "query", "content")),
    re.DOTALL | re.IGNORECASE,
)
_AGENT_KEYS = ("agent_name", "agent", "name", "sub_agent", "target")
_TASK_KEYS = ("task", "instruction", "prompt", "input", "query", "content")

# 仅匹配显式标注 ```python / ```py 的围栏代码块；不匹配 ```json / ```bash 等其他语言，
# 也不匹配无标注代码块——避免把示例/说明性质的代码片段误判为「要执行的代码」。
# 用于「弱模型把 Python 写成 Markdown 代码块而非发起 function call」的兜底执行。
_PY_FENCE_RE = re.compile(
    r"```(?:python|py)[ \t]*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_balanced(text: str, lparen: int) -> tuple[str, int]:
    """从 text[lparen]=='(' 起做「感知引号」的括号配对，返回 (括号内文本, 右括号后位置)。

    需要感知引号，否则 task 内容里出现 ')' 会导致提前截断。
    """
    depth, i, n = 0, lparen, len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = ""
                continue
            i += 1
            continue
        if ch in "\"'":
            for q in ('"""', "'''"):
                if text.startswith(q, i):
                    quote, i = q, i + 3
                    break
            else:
                quote, i = ch, i + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[lparen + 1:i], i + 1
        i += 1
    return "", -1


def _lit(node) -> Optional[str]:
    try:
        v = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    return v if isinstance(v, str) else (str(v) if v is not None else None)


def _coerce_delegate_args(inner: str) -> dict:
    """把 delegate_to_agent(...) 括号内文本转成 {agent_name, task}。"""
    inner = (inner or "").strip()
    if not inner:
        return {}
    agent = task = None
    # 1) 按 Python 调用表达式解析：支持关键字参数 / 位置参数 / 三引号 / 多行 / dict 入参
    try:
        node = ast.parse(f"_f({inner})", mode="eval").body
        for kw in getattr(node, "keywords", []) or []:
            val = _lit(kw.value)
            if val is None:
                continue
            if kw.arg in _AGENT_KEYS:
                agent = agent or val
            elif kw.arg in _TASK_KEYS:
                task = task or val
        pos = list(getattr(node, "args", []) or [])
        if pos and isinstance(pos[0], ast.Dict):  # delegate_to_agent({"agent_name": ...})
            try:
                d = ast.literal_eval(pos[0])
            except Exception:  # noqa: BLE001
                d = {}
            if isinstance(d, dict):
                for k in _AGENT_KEYS:
                    agent = agent or (str(d[k]) if d.get(k) else None)
                for k in _TASK_KEYS:
                    task = task or (str(d[k]) if d.get(k) else None)
        else:
            vals = [v for v in (_lit(a) for a in pos) if v]
            if agent is None and vals:
                agent = vals[0]
                if task is None and len(vals) > 1:
                    task = vals[1]
    except (SyntaxError, ValueError):
        pass
    # 2) 正则兜底：模型写出非法 Python（如 JSON 的 true/null、缺引号）时仍能救回
    if not agent:
        m = re.search(r"(?:%s)\s*[:=]\s*[\"']([^\"'\n]+)[\"']" % "|".join(_AGENT_KEYS), inner)
        agent = m.group(1) if m else None
    if agent and not task:
        _tk = "|".join(_TASK_KEYS)
        # 先按「task 是最后一个参数」匹配（可含引号、换行）；不中再退回非贪婪单行匹配
        m = (re.search(r"(?:%s)\s*[:=]\s*[\"']([\s\S]*)[\"']\s*[,)]?\s*$" % _tk, inner)
             or re.search(r"(?:%s)\s*[:=]\s*[\"']([^\"'\n]*)[\"']" % _tk, inner))
        task = m.group(1) if m else None
    if not agent:
        return {}
    return {"agent_name": agent.strip(), "task": (task or "").strip()}


def _xml_tool_call_to_python(text: str) -> str:
    """把 XML 形式的 delegate 调用转成 delegate_to_agent(...)。"""
    out = text
    # 1) <tool_call>delegate_to_agent agent_name="X" task="Y"</tool_call>
    for m in _DELEGATE_XML_RE.finditer(text):
        inner = m.group(1).strip()
        out = out.replace(m.group(0), f"delegate_to_agent({inner})")

    # 2) <tool_call>delegate_to_agent(agent_name="X", task="Y")</tool_call>
    #    这类直接就是合法函数调用，只需去掉外层 XML 标签即可。
    out = _DELEGATE_XML_PAREN_RE.sub(r"\1", out)

    # 3) <delegate_to_agent><agent_name>X</agent_name><task>Y</task>...</delegate_to_agent>
    def _replace_block(m: re.Match) -> str:
        block = m.group(1)
        kvs = []
        for tag_m in _DELEGATE_XML_TAG_RE.finditer(block):
            key = tag_m.group(1).lower()
            val = tag_m.group(2).strip()
            # 统一映射到标准关键字
            if key in _AGENT_KEYS:
                key = "agent_name"
            elif key in _TASK_KEYS:
                key = "task"
            else:
                continue
            # 对值中的双引号做转义，方便直接拼成 Python 字符串字面量
            val = val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            kvs.append(f'{key}="{val}"')
        return f"delegate_to_agent({', '.join(kvs)})"

    out = _DELEGATE_XML_BLOCK_RE.sub(_replace_block, out)
    return out


def _parse_delegate_calls(text: str, limit: int = 6) -> list[dict]:
    """从模型文本中解析出所有 delegate_to_agent(...) 调用。

    支持两种文本形式：
      1) 常规文本 / Markdown 代码块：delegate_to_agent(agent_name="X", task="Y")
      2) XML tool_call 标签：<tool_call>delegate_to_agent agent_name="X" task="Y"</tool_call>
    """
    out: list[dict] = []
    if not text or "delegate_to_agent" not in text:
        return out
    # 统一预处理 XML 标签格式
    text = _xml_tool_call_to_python(text)
    pos = 0
    while len(out) < limit:
        m = _DELEGATE_CALL_RE.search(text, pos)
        if not m:
            break
        inner, nxt = _extract_balanced(text, m.end() - 1)
        if nxt < 0:
            break
        pos = nxt
        args = _coerce_delegate_args(inner)
        if args.get("agent_name"):
            out.append(args)
    return out


def _extract_python_blocks(text: str) -> list[str]:
    """从模型文本中提取所有显式标注 ```python / ```py 的代码块内容。

    仅匹配带 python/py 语言标注的围栏块（见 ``_PY_FENCE_RE``），不匹配其他语言块，
    也不匹配无标注代码块，避免把示例/说明性质的代码片段误当成要执行的代码。

    用于「弱模型把 Python 写成 Markdown 代码块而非发起 function call」的兜底执行：
    抽出的代码会走与 function call 完全一致的 code_exec 执行路径，真正运行而非只贴文本。
    """
    if not text:
        return []
    out: list[str] = []
    for m in _PY_FENCE_RE.finditer(text):
        body = m.group(1)
        if body and body.strip():
            out.append(body)
    return out


def _expected_sub_agents(meta: dict) -> list[str]:
    """推断协调类 Agent 应当覆盖的子智能体清单（用于收尾前强制补齐）。

    - 优先使用 agent.json 的 ``sub_agents`` 显式字段；
    - 否则从协调类 Agent 的 system_prompt/description 中匹配全局已知 Agent 名
      （弱模型常漏派某个子智能体，据此可在收尾前强制补齐，避免「只委派部分」）。
    """
    meta = meta or {}
    custom = meta.get("sub_agents")
    if isinstance(custom, list) and custom:
        return [str(x) for x in custom if x]
    text = " ".join(str(meta.get(k, "")) for k in ("system_prompt", "description", "name"))
    names: list[str] = []
    try:
        from ..agents_runtime import list_agents
        # 注意：必须以 admin 视角列举全部已启用子智能体。协调类据此推断「应覆盖清单」，
        # 用于收尾前强制补齐漏派的子智能体（如弱模型漏派 Risk）。若以匿名/默认视角调用
        # list_agents()，私有（shared=false）子智能体不可见，会导致 expected 为空、覆盖度闸门
        # 永远判定「无缺失」而失效。
        for a in list_agents(is_admin=True):
            n = a.get("name")
            if not n:
                continue
            if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text):
                names.append(n)
    except Exception:
        pass
    return names


# 闲聊/寒暄/极短问句：主管可直接作答，不触发「强制委派协调类智能体」兜底。
_CHITCHAT_RE = re.compile(
    r"^\s*(?:[你您]好|哈[喽啰]|嗨|在吗|在么|谢谢|多谢|辛苦了|再见|拜拜|早上?好|晚上?好|"
    r"hi|hello|hey|thanks?|thank\s*you|bye|ok|okay|测试|test)"
    r"[\s!！。.~、,，?？]*$",
    re.IGNORECASE,
)


# 显式请求「组建团队 / 子智能体」：必须建团或委派给协调者。
_EXPLICIT_TEAM_RE = re.compile(
    r"创建?(团队|team)|组(建|个)?(团队|agent)|建(一个|个)?(子智能体|agent|智能体)|"
    r"委派(给|到|给到)|用\s*(orchestrator|协调者|投资总监)|"
    r"多(个|角色).{0,4}(协作|分工|智能体)|让\s*.{0,6}(负责|处理|分析)",
    re.IGNORECASE,
)
# 显式点名「某团队」：仅当消息中出现团队名（领域+团队/组，或已知协调者名）才路由，
# 避免把「股票分析」等词在能力咨询/闲聊中顺带提到就误触发多 Agent 协作（资源浪费）。
# 必须是「领域+团队/组」或「用/让/请…团队」结构——普通「我们团队/他们团队」不命中。
_DOMAIN_TEAM = (
    r"(?:股票|证券|基金|投资|量化|行业|产业|市场|业务|商业|财务|金融|风控|风险|"
    r"研究|调研|项目|产品|技术|工程|代码|数据|运营|营销|用户|竞品|法律|合规|"
    r"医疗|教育|供应链|人力|战略|品牌|能源|制造|消费)"
)
_TEAM_NAME_RE = re.compile(
    r"(?:用|让|请|交给|派|安排|由|叫|召唤)\s*[\"'\u201c\u201d]?[一-鿿A-Za-z0-9_]{0,10}?"
    rf"(?:{_DOMAIN_TEAM})?(?:分析|研究|调研|评估|投研|咨询)?团队"
    rf"|(?:{_DOMAIN_TEAM})(?:分析|研究|调研|投研|咨询)?团队"
    r"|投资总监|协调者|总监|Orchestrator",
    re.IGNORECASE,
)
# 显式要求「多智能体协作 / 多模型并行」模式（不走具名团队，而是 MoA/并行聚合）。
_MULTIAGENT_RE = re.compile(
    r"多智能体|多模型|多\s*agent|moa\s*模式|并行\s*(多模型|多个模型|多智能体)|"
    r"多角色\s*(协作|分工)",
    re.IGNORECASE,
)


def _needs_supervisor_delegation(text: str) -> bool:
    """判断主管是否「必须」把该任务委派给协调类智能体（路径 B）。

    设计原则（对标 Hermes 委派路由）：路由由**服务端分类器权威决定**，而非仅靠
    系统提示软约束让模型自律——否则弱模型会把普通问题也丢进多 Agent 协作，浪费资源。

    **默认不委派（路径 A）**。仅在以下「显式」信号出现时才委派（路径 B）：
      1) 用户显式要求组建/创建团队、或显式委派给某协调者（如 Orchestrator/投资总监）；
      2) 用户**显式点名某团队**（如「股票分析团队」「业务分析团队」「用股票分析团队」）；
      3) 用户显式要求「多智能体协作 / 多模型并行」模式。

    普通问题、能力咨询、闲聊，以及**仅顺带提及领域词**（如「除了股票分析你还能做什么」、
    「帮我分析一下这段代码」）一律按路径 A 直接作答，绝不进入多 Agent 协作。
    """
    t = (text or "").strip()
    if len(t) < 4:
        return False
    if _CHITCHAT_RE.match(t):
        return False
    # 仅「显式点名团队」或「显式要求多智能体协作」才走路径 B；其余一律路径 A。
    if _EXPLICIT_TEAM_RE.search(t):
        return True
    if _TEAM_NAME_RE.search(t):
        return True
    if _MULTIAGENT_RE.search(t):
        return True
    return False


class Agent:
    def __init__(
        self,
        cfg: ZhishuConfig,
        llm: LLMClient,
        kb: Optional[KnowledgeBase] = None,
        memory: Optional[MemoryStore] = None,
        ctx: Optional[ToolContext] = None,
        media: Optional = None,
        context_engine: Optional[ContextEngine] = None,
        memory_manager: Optional[MemoryManager] = None,
    ):
        self.cfg = cfg
        self.llm = llm
        self.kb = kb
        self.memory = memory
        # 修复（闭环回归 H1）：生产链路传入的 ctx 恒为非 None 的 ToolContext 实例，
        # 且 ToolContext 未定义 __bool__，故 `ctx or ...` 会永远取 ctx，导致构造入参
        # media 被丢弃 -> self.ctx.media 为 None，工具无法自动发布 /media 下载链接、
        # 下载护栏自愈失效。这里在 ctx 自身未携带 media 时，用构造入参 media 补齐
        # （for_run 浅拷贝会保留 media）。
        self.ctx = ctx if ctx is not None else ToolContext(kb=kb, security=cfg.security, media=media)
        self.media = media
        if self.ctx.media is None and media is not None:
            from dataclasses import replace as _dc_replace
            self.ctx = _dc_replace(self.ctx, media=media)
        self.context_engine = context_engine or NoOpContextEngine()
        # 外部长期记忆（向量 provider，opt-in）。为 None 时 prefetch/sync 全为 no-op，零回归。
        self.memory_manager = memory_manager
        # create_team 成功后，把团队清单作为醒目前缀拼到主管最终回复开头。
        self._team_roster_preface = ""

    async def run(
        self,
        user_message: str,
        session: str = "default",
        model: Optional[str] = None,
        image: Optional[str] = None,
        owner: Optional[str] = None,
        agent_name: Optional[str] = None,
        attachments: Optional[list] = None,
        is_admin: bool = False,
        user_role: Optional[str] = None,
        delegate_depth: int = 0,
    ) -> AsyncIterator[dict]:
        # 让工具（知识库/文件等）能感知当前用户，按归属隔离文档。
        # 安全（防并发串号）：绝不在共享单例 ToolContext 上就地改 user ——
        # 并发请求会互相覆盖身份。这里派生**本次运行专用**的副本，并把身份
        # 写入 contextvars（task-local），owner 为空时 fail-closed 落到 anonymous，
        # 绝不继承上一个请求的身份。
        self.ctx = self.ctx.for_run(owner, session, is_admin=is_admin, user_role=user_role)
        set_current_user(self.ctx.user, is_admin=is_admin, user_role=user_role)
        # 按用户隔离模型：把 cfg/llm 收窄为「当前 owner 可见」的配置副本
        # （仅本人 + 共享 Provider + 角色命中 Provider 及其默认模型）。admin 视为可见全部，返回原 cfg。
        # 注意：Agent 实例为每轮请求独立创建，此处改写 self.cfg/self.llm 不会产生并发串号。
        # 资源：LLMClient 已改为共享进程级 httpx 连接池（core/providers/client.py），
        # 构造实例本身零开销、不再每轮泄漏一个连接池，故此处重建是安全的。
        if owner:
            self.cfg = self.cfg.for_user(owner, is_admin, user_role=user_role)
            self.llm = LLMClient(self.cfg, self.llm.api_mode)
        # ---- 0. 按模型类型分流：图像 / 视频 走生成分支，文本走 ReAct 循环 ----
        try:
            pc, mdl = self.cfg.resolve_model(model)
            kind = classify_model(mdl)
        except Exception:
            pc, mdl, kind = None, None, "text"

        if image and kind == "text" and pc is not None:
            alt = self._first_model_of_kind(pc, "image")
            if alt:
                mdl, kind = alt, "image"

        if kind == "image" and pc is not None:
            if image:
                mdl = self._pick_img2img_model(pc, mdl)
            async for ev in self._run_image(user_message, session, pc, mdl, image=image):
                yield ev
            return
        if kind == "video" and pc is not None:
            async for ev in self._run_video(user_message, session, pc, mdl, image=image):
                yield ev
            return

        # 每轮复位本轮工具产出的 /media 链接收集（防止跨轮 / 跨子智能体累积污染）
        self._turn_media_links = []
        # ---- 1. 组装上下文（分层系统提示）----
        # 先发一个状态事件保活 SSE：知识库检索（向量相似度）/ 系统提示组装可能耗时
        # （尤其首轮需惰性加载数 GB 向量索引）。若首字节迟迟不发，前端 / 反向代理的读
        # 超时会被触发，浏览器侧表现为「network error」。提前 yield 可避免该假死。
        yield {"type": "status", "text": "正在检索知识库并组织上下文…"}
        # 上下文组装涉及同步的 KB 检索与分词，放到线程池执行，避免阻塞事件循环与 SSE 推送。
        try:
            system, has_coordinator = await asyncio.to_thread(
                build_system_prompt,
                self.cfg, agent_name=agent_name, owner=owner,
                kb=self.kb, query=user_message, is_admin=is_admin, user_role=user_role,
            )
        except Exception as e:
            # 上下文组装异常（知识库/分词等）若冲出生成器会掐断 SSE → 浏览器报 network error。
            yield {"type": "error", "message": f"上下文组装失败：{e}。请检查知识库配置后重试。"}
            yield {"type": "done"}
            return

        # 长期记忆召回（向量记忆 opt-in；无外部 provider 时返回空串，零回归）。
        # 放在系统提示末尾作为 volatile 上下文，不污染身份（stable）部分。
        if self.memory_manager is not None:
            try:
                mem_ctx = await asyncio.to_thread(
                    self.memory_manager.prefetch, owner, user_message
                )
            except Exception:
                mem_ctx = ""
            if mem_ctx:
                system = system + "\n\n" + mem_ctx

        messages = [{"role": "system", "content": system}]
        if self.memory:
            history = self.memory.history(session, limit=20)
            # 上下文压缩（ContextEngine 可插拔；NoOp 时仅做窗口守护）。
            # compress_history 为异步方法（会调用 LLM 摘要），必须在事件循环中 await。
            # 传入本轮实际模型，使「模型管理」里配置的 context_length 生效于历史裁剪。
            history = await self.context_engine.compress_history(session, history, model)
            for h in history:
                c = h["content"]
                # 历史消息若含视觉图片部件（列表型 content），仅保留文本部分，
                # 避免后续每轮都把已发送过的巨幅 base64 图片重复塞给模型（Hermes 同理按发送次附加）。
                if isinstance(c, list):
                    c = [p for p in c if p.get("type") != "image_url"]
                messages.append({"role": h["role"], "content": c})
        # ---- 1.5 多模态附件（对标 Hermes 图片/PDF 处理：native 视觉 / 上下文提示）----
        # 默认纯文本；当存在视觉部件时改为 content 列表 [text, image_url...]。
        user_content = user_message
        if attachments and kind == "text":
            try:
                user_content = self._build_multimodal_content(
                    user_message, attachments, mdl)
            except Exception as e:
                # 图片/附件多模态内容组装异常（转码、读取等）若冲出生成器会掐断 SSE
                # → 浏览器报 network error。转为清晰错误事件。
                yield {"type": "error", "message": f"图片/附件处理失败：{e}。请确认文件未损坏或格式受支持。"}
                yield {"type": "done"}
                return
        messages.append({"role": "user", "content": user_content})

        if self.memory:
            self.memory.append(session, "user", user_message)
        if self.memory_manager is not None:
            await self._sync_memory(owner, "user", user_message)

        # ---- LLM 可用性兜底：无可用 Provider / 未配置模型 → 优雅报错而非掐断 SSE ----
        # 否则异常会冲出 SSE 生成器，导致响应流被中断，浏览器侧表现为「network error」。
        if self.llm is None:
            yield {"type": "error", "message":
                   "未配置可用的 LLM（没有任何 enabled 的 Provider）。请先在配置中启用 Provider 并填写 "
                   "API Key，或启动本地推理服务（Ollama / vLLM）后重试。"}
            yield {"type": "done"}
            return

        # 委派能力判定（按「深度」而非「是否命名 Agent」）：
        # 顶层运行 delegate_depth=0，无论用户选的是「主管（自动编排）」还是某个具名
        # 编排型 Agent（如 Orchestrator 投资总监），都应当能委派；被委派出去的子智能体
        # depth=1，达到上限后不再暴露委派工具，从根上杜绝 A→B→A 递归风暴。
        try:
            _max_depth = int(getattr(self.cfg.agent, "delegate_max_depth", 3) or 3)
        except (TypeError, ValueError):
            _max_depth = 3
        can_delegate = delegate_depth < _max_depth

        # 主管模式下可委派的「协调类智能体」名单（tools 显式含 delegate_to_agent，如 Orchestrator）。
        # 供「主管一次都没委派」时的系统兜底代委派使用。
        _sup_coordinators: list[str] = []

        # 工具集：子智能体按 tools 字段裁剪；主管使用全部（含委派工具）。
        # 多用户隔离：plugin__/mcp__ 工具按归属过滤（共享 + 本人；admin 全量），
        # 防止 A 用户的 Agent 调用 B 用户的私有插件/MCP 工具。
        _base_max = self.cfg.agent.max_steps or MAX_STEPS
        if agent_name:
            specs = resolve_tools(get_agent_meta(agent_name).get("tools", "all"),
                                  username=owner, is_admin=is_admin, user_role=user_role)
            if not can_delegate:
                specs = [s for s in specs if s["function"]["name"] != DELEGATE_TOOL_NAME]
            try:
                max_steps = int(get_agent_meta(agent_name).get("max_steps") or _base_max)
            except (TypeError, ValueError):
                max_steps = _base_max
        else:
            specs = filter_tool_specs(ToolRegistry.specs(), owner, is_admin, user_role)
            if not can_delegate:
                specs = [s for s in specs if s["function"]["name"] != DELEGATE_TOOL_NAME]
            # 主管委派路由（对标 Hermes：路由由服务端分类器权威决定）。
            # 路径 B · 复合任务（需要多角色/多步骤协作）：强制走委派链路——
            #   裁剪成元工具 + 委派，并收集协调者名单供兜底代委派。
            # 路径 A · 简单问答 / 能力咨询：主管直接作答，**权威移除 delegate_to_agent /
            #   create_team**，使模型物理上无法把普通问题丢进多 Agent 协作（根治资源浪费）。
            need_del = _needs_supervisor_delegation(user_message)
            if can_delegate and has_coordinator and need_del:
                allowed = {
                    "delegate_to_agent", "create_team", "session_search", "memory", "todo",
                    "knowledge_search", "knowledge_list", "knowledge_read",
                    "file_read", "file_list", "read_skill",
                }
                specs = [s for s in specs if s["function"]["name"] in allowed]
                # 收集协调类智能体名单（与 _delegate_catalogue 的判定口径一致）
                try:
                    from ..agents_runtime import list_agents as _list_agents
                    for _a in _list_agents(username=owner, is_admin=is_admin,
                                           user_role=user_role):
                        _t = _a.get("tools")
                        if (_a.get("enabled") and isinstance(_t, list)
                                and DELEGATE_TOOL_NAME in _t and _a.get("name")):
                            _sup_coordinators.append(_a["name"])
                except Exception:
                    pass
            elif can_delegate and has_coordinator and not need_del:
                specs = [s for s in specs
                         if s["function"]["name"] not in
                         {"delegate_to_agent", "create_team"}]
            max_steps = _base_max

        # ---- MoA 多智能体 facade：把单轮对话路由到并行聚合 ----
        if pc is not None and getattr(pc, "mode", "") == "moa":
            from .moa import MoAClient

            yield {"type": "status", "text": "正在以多智能体(MoA)模式协同作答…"}
            answer = ""
            async for piece in MoAClient(self.cfg).stream(
                messages, model=model, tools=specs if specs else None,
                max_tokens=self.cfg.agent.max_tokens,
            ):
                answer += piece
                yield {"type": "token", "text": piece}
            if self.memory:
                self.memory.append(session, "assistant", answer)
            if self.memory_manager is not None:
                await self._sync_memory(owner, "assistant", answer)
            yield {"type": "done"}
            return

        # 协调类智能体判定（tools 显式含 delegate_to_agent）：
        # 这类智能体（如 Orchestrator）的职责就是分派子任务，必须强制其第一轮就发起委派。
        _meta_tools = get_agent_meta(agent_name).get("tools") if agent_name else None
        is_coordinator = (
            agent_name
            and isinstance(_meta_tools, list)
            and DELEGATE_TOOL_NAME in _meta_tools
        )
        # 调试：记录协调类判定结果
        try:
            from ...context import get_ctx
            if getattr(get_ctx(), "audit", None) is not None:
                get_ctx().audit.log(
                    self.ctx.user or "anonymous",
                    "coordinator_check",
                    f"agent={agent_name} tools={_meta_tools} is_coordinator={is_coordinator}",
                )
        except Exception:
            pass

        # 弱模型识别：glm/sensenova/qwen-turbo/deepseek 等并行 function-call 不可靠，
        # 但 XML 文本形式能稳定列全所有子智能体。对协调类弱模型剥除 delegate_to_agent 工具，
        # 强制走「文本委派解析」分支（can_delegate=True 时生效），避免只委派部分/重复委派。
        _coordinator_weak = False
        if is_coordinator:
            _ml = (model or "").lower()
            if any(k in _ml for k in ("glm", "sensenova", "agnes1-flash",
                                      "qwen-turbo", "qwen2.5", "deepseek", "abab")):
                _coordinator_weak = True
                specs = [s for s in specs if s["function"]["name"] != DELEGATE_TOOL_NAME]

        # ---- 2. 推理循环 ----
        # 轨迹采集（供技能自进化闭环使用）：记录工具调用与累计步数/工具数。
        traj_tools: list[dict] = []
        tool_total = 0
        # 反空转熔断状态（Task #395）：仅对「失败/被拦截」的工具按签名累计，
        # 避免误伤正常任务中偶发的成功重复调用（如重复读取同一文件）。
        _breaker_sig: dict = {}
        _consec_fail = 0
        # 确定性拦截（[已拦截]）按签名累计（仅用于统计/可观测，不再据此硬终止——
        # 拦截是确定性结果，重复必再被拦，但任务不再被杀，改由 max_steps 预算兜底 + 首触提醒）。
        _breaker_blocked: dict = {}
        # 重复成功循环（Task #399）：同一 (工具名, 归一化参数) 被反复调用且均成功返回，
        # 与失败计数解耦——正常任务偶发重读不触发，但反复 read_file 同一文档会在阈值内转为跳过。
        _breaker_repeat: dict = {}
        # 重复成功循环「停止重复」提醒去重：同一签名仅注入一次系统提醒，
        # 后续命中只把工具结果替换为跳过提示，避免刷屏；运行不终止。
        _repeat_nudged: set = set()
        # 每个被拦签名仅注入一次「勿重试」系统提醒，引导模型改用其他命令/工具。
        _blocked_nudged: set = set()
        # 连续失败 / 工具步数接近上限 的「软提醒」去重集合（仅注入一次系统提醒，不终止）。
        _fail_nudged: set = set()
        _toolcap_nudged: set = set()
        # 收尾提醒（grace call，对标 Hermes _budget_grace_call）去重：预算耗尽前仅注入一次，
        # 提示 Agent 给出最终结论而非半路被截断。
        _finalize_nudged: bool = False
        # 文本委派去重：防止弱模型反复输出同一个 delegate_to_agent(...) 空转步数。
        # 仅按子智能体名去重（忽略 task 文本差异）——弱模型常把同一子智能体换个说法反复
        # 重派，若按 task 前缀去重会把「换汤不换药」的重派误判为新委派，导致 stall 计数永不
        # 累积、覆盖度闸门永远到不了，进而漏派某个子智能体（如 Risk）。按名去重后，重复重派
        # 一律视为空转，stall 正常累积，最终落入覆盖度闸门兜底。
        _delegated_seen: set[str] = set()
        _delegate_stall = 0
        # 已实际委派的子智能体（跨分支去重 + 收尾前覆盖度校验）
        _delegated_agents: set[str] = set()
        _coverage_nudges = 0
        # 协调类应覆盖的子智能体清单（从 system_prompt/registry 推断），运行期恒定，提前算一次。
        # 供覆盖度闸门与「重复重派空转」判定共用，避免弱模型漏派时闸门到不了。
        _expected_cache: list[str] = (
            _expected_sub_agents(get_agent_meta(agent_name) if agent_name else {})
            if is_coordinator else []
        )
        # 主管强制委派：存在协调类子智能体（如 Orchestrator）且本次为复合任务时，
        # 主管不得自行执行/直接作答，必须整体委派给协调类智能体。弱模型（glm 等）常无视
        # 系统提示直接作答（表现为「主管没有编排委派」），故除提示外还需系统兜底代委派。
        _sup_must_delegate = bool(
            can_delegate and has_coordinator and not agent_name
            and _sup_coordinators and _needs_supervisor_delegation(user_message)
        )
        _sup_nudges = 0
        for step in range(max_steps):
            _did_delegate_this_step = False  # 本轮是否实际产生了委派
            # 收尾提醒（grace call，对标 Hermes _budget_grace_call）：预算接近耗尽时仅注入一次，
            # 提示 Agent 停止发起新工具调用、基于已有结果给出最终结论，避免在半路被截断。
            if step >= max_steps - 2 and not _finalize_nudged:
                _finalize_nudged = True
                messages.append({
                    "role": "system",
                    "content": (
                        f"[系统] 你已接近推理步数上限（上限 {max_steps}）。这是最后几次机会："
                        f"请停止发起新的工具调用或新任务，基于已有结果直接给出最终结论/交付物。"
                        f"若确实还需一步工具，请尽快完成并立刻总结。"
                    ),
                })
            # 协调类智能体第一轮强制调用工具：tool_choice="required" 确保模型
            # 必须输出至少一次 function call（delegate_to_agent），避免其只输出计划文字。
            # 但弱模型（glm/sensenova 等）的并行 function-call 不可靠（重复/漏派），
            # 其 XML 文本形式反而能稳定列全所有子智能体——故弱模型改为剥除 delegate 工具、
            # 走文本委派解析分支（can_delegate=True 时生效）。
            _tool_choice = "auto"
            if step == 0 and (
                (is_coordinator and not _coordinator_weak) or _sup_must_delegate
            ):
                _tool_choice = "required"
            try:
                resp = await self.llm.chat(
                    messages, model=model, tools=specs if specs else None,
                    max_tokens=self.cfg.agent.max_tokens,
                    tool_choice=_tool_choice,
                )
                # 防御：上游网关/代理可能返回「200 + 错误 JSON」而非抛异常，
                # 归一化后缺少 choices；此处显式校验，避免下方 resp["choices"] 触发 KeyError
                # 冲出 SSE 生成器（浏览器侧表现为「network error」）。
                if not isinstance(resp, dict) or "choices" not in resp:
                    raise RuntimeError(f"模型返回非预期响应：{str(resp)[:200]}")
                choice = resp["choices"][0]["message"]
                # 记录 finish_reason：若为 "length" 表示本次生成被 max_tokens 截断，
                # 最终回答阶段需续写补全（见下方 final 分支），避免「回复内容不完整」。
                finish_reason = resp["choices"][0].get("finish_reason", "stop")
            except Exception as e:
                # Provider 全部不可用 / 网络不可达 / 鉴权失败 / 响应畸形等：转为清晰错误事件，
                # 避免异常冲出 SSE 生成器导致响应流中断（浏览器侧表现为「network error」）。
                yield {"type": "error",
                       "message": f"模型服务调用失败：{e}。请检查 LLM Provider 配置（API Key / base_url）"
                                  f"或本地推理服务（Ollama / vLLM）是否可用。"}
                yield {"type": "done"}
                return
            tool_calls = choice.get("tool_calls")

            if tool_calls:
                # ---- 并行/串行预处理：先解析全部 tool_calls，分离委派与叶子工具 ----
                # 同一响应若混有委派（delegate_to_agent）则整体串行（委派须保序且含复杂子
                # 流程）；否则当叶子工具≥2 时并发执行以缩短多工具步耗时（对标 Hermes 分段并行）。
                _parsed: list = []
                _any_delegate = False
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if name == DELEGATE_TOOL_NAME and can_delegate:
                        _any_delegate = True
                    _parsed.append((tc, name, args))
                _leaf_count = sum(
                    1 for _, n, _ in _parsed if not (n == DELEGATE_TOOL_NAME and can_delegate))
                _do_parallel = (self.cfg.agent.parallel_tools and not _any_delegate and _leaf_count >= 2)
                if _do_parallel:
                    # 并发执行所有叶子工具（I/O 密集，安全），结果按原序收集；
                    # 其余副作用（落盘/消息回填/事件流）仍保持串行原序回放，保证行为等价。
                    _sem = asyncio.Semaphore(max(1, self.cfg.agent.parallel_tool_workers))

                    async def _exec_leaf(_i, _nm, _a):
                        async with _sem:
                            return await ToolRegistry.execute(_nm, _a, self.ctx)

                    _leaf_results = await asyncio.gather(
                        *[_exec_leaf(_i, _nm, _a) for _i, (_tc, _nm, _a) in enumerate(_parsed)]
                    )
                else:
                    _leaf_results = None
                for _li, (tc, name, args) in enumerate(_parsed):
                    # 委派调用去重：同一次运行内对同一子智能体重复委派直接跳过执行，
                    # 避免弱模型反复委派同一子智能体（如 Research×3）空转耗尽步数。
                    if name == DELEGATE_TOOL_NAME and can_delegate:
                        _dname = args.get("agent_name") or args.get("agent")
                        if _dname in _delegated_agents:
                            messages.append({
                                "role": "assistant",
                                "content": choice.get("content"),
                                "tool_calls": tool_calls,
                            })
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id"),
                                "content": f"[跳过] 已委派过 {_dname}，结果见上文，请勿重复委派。",
                            })
                            yield {"type": "tool_result", "name": name,
                                   "result": f"[跳过] 已委派过 {_dname}", "agent": _dname}
                            continue
                        _delegated_agents.add(_dname)
                    yield {"type": "tool_call", "name": name, "args": args}
                    if name == DELEGATE_TOOL_NAME and can_delegate:
                        _did_delegate_this_step = True
                        result = ""
                        async for ev in self._run_delegate(args, session, owner, is_admin,
                                                          user_role=user_role,
                                                          depth=delegate_depth + 1,
                                                          parent=agent_name):
                            if ev.get("type") == "delegate_end":
                                # 捕获最终结果用于主消息，同时将 delegate_end 转发给前端
                                # 以关闭委派 UI（与 delegate_start 对称）
                                result = ev.get("result", "")
                            yield ev
                        yield {"type": "tool_result", "name": name, "result": result,
                               "agent": args.get("agent_name") or args.get("agent")}
                        messages.append({
                            "role": "assistant",
                            "content": choice.get("content"),
                            "tool_calls": tool_calls,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": result,
                        })
                        continue
                    # ---- 获取工具结果：并行分支直接取预取值，否则串行执行 ----
                    if _do_parallel:
                        result = _leaf_results[_li]
                    else:
                        result = await ToolRegistry.execute(name, args, self.ctx)
                    # ---- 反空转熔断计数（Task #395）：仅对失败/被拦截结果累计签名 ----
                    # 成功调用会使连续失败计数清零，但签名累计仍保留，从而能捕获
                    # 「成功/失败交替」的死循环（如 code_exec 装 Node 成功、terminal_run
                    # 装 Node 被白名单拦截，交替往复）。
                    _sig_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
                    if len(_sig_args) > 400:
                        _sig_args = _sig_args[:400]
                    _sig = f"{name}\u0001{_sig_args}"
                    # 重复成功循环（Task #399）：无论成功失败，按完整签名累计相同调用次数，
                    # 专门捕获「反复 read_file 同一文档」式纯成功停滞（无失败、连续失败计数为 0）。
                    _breaker_repeat[_sig] = _breaker_repeat.get(_sig, 0) + 1
                    _is_fail = (result or "").startswith(("[工具错误]", "[工具执行异常]", "[已拦截]"))
                    # 确定性拦截：安全策略（白名单 / allow_shell / 角色）拒绝，重复相同调用必再被拦
                    _is_block = (result or "").startswith("[已拦截]")
                    _block_reason = (result or "")[len("[已拦截]"):].strip() if _is_block else ""
                    # 区分「命令语法/结构错误」与「安全策略配置拦截」：前者（引号不闭合、
                    # 空命令、命令过长、含空字节、裸命令替换、环境变量前缀）与 security 配置
                    # 无关，重复相同坏命令必然再被拦；后者才是 allow_shell/白名单/角色问题。
                    _is_syntax_err = _is_block and any(
                        k in (_block_reason or "") for k in (
                            "引号不闭合", "命令为空", "命令过长", "包含空字节",
                            "命令替换", "环境变量前缀", "无法安全解析",
                        ))
                    if _is_fail:
                        _breaker_sig[_sig] = _breaker_sig.get(_sig, 0) + 1
                        _consec_fail += 1
                        if _is_block:
                            _breaker_blocked[_sig] = _breaker_blocked.get(_sig, 0) + 1
                    else:
                        _consec_fail = 0
                    # 收集本轮工具产出的 /media 下载链接，供最终回复护栏兜底
                    # （即使模型最终没透传链接，也能强制补回，见 guard_download_links）
                    if "/media/" in (result or ""):
                        _ml = extract_media_links(result)
                        if _ml:
                            _col = getattr(self, "_turn_media_links", None)
                            if _col is None:
                                _col = []
                                self._turn_media_links = _col
                            _col.extend(_ml)
                    # 动态建团队（create_team）成功后：刷新可委派协调者清单、置位强制委派标志，
                    # 并注入系统提醒，使主管下一步把用户原始任务整体委派给新建协调者，形成
                    # 主管 → 协调者 → 成员 的完整链路（无需预配置任何 agent）。
                    if name == "create_team" and "团队创建成功" in result:
                        try:
                            from ..agents_runtime import list_agents as _la
                            _all = {a["name"]: a for a in _la(
                                username=owner, is_admin=is_admin, user_role=user_role)}
                            _sup_coordinators = [
                                a["name"] for a in _all.values()
                                if a.get("enabled") and isinstance(a.get("tools"), list)
                                and DELEGATE_TOOL_NAME in a["tools"] and a.get("name")
                            ]
                            if _sup_coordinators:
                                has_coordinator = True
                                _sup_must_delegate = True
                                _sup_nudges = 0
                                coord = _sup_coordinators[0]
                                co_meta = _all.get(coord, {})
                                subs = co_meta.get("sub_agents") or []
                                roster = [f"- 🎯 **{coord}**（{co_meta.get('description', '')}）"]
                                for s in subs:
                                    sm = _all.get(s, {})
                                    if sm:
                                        roster.append(f"- 🔹 **{s}**（{sm.get('description', '')}）")
                                roster_block = "\n".join(roster)
                                # 硬拼接到主管最终回复开头，避免模型忽略 system 提示导致信息淹没
                                self._team_roster_preface = (
                                    f"## ✅ 已创建多智能体团队（可在左侧「智能体」菜单查看）\n"
                                    f"{roster_block}\n"
                                    f"\n> 这些智能体已就绪：后续可在对话中直接说「让 {coord} 处理…」"
                                    f"来调度整个团队。\n\n---\n\n"
                                )
                                messages.append({
                                    "role": "system",
                                    "content": (
                                        f"[系统] 已为你创建多智能体团队，协调者={coord}。\n"
                                        f"请立即将用户的原始任务整体委派给协调者 {coord}，"
                                        f"由其内部分派给各成员并汇总；不要自行执行专业研究步骤。\n\n"
                                        f"【重要】在面向用户的最终回复**最开头**，必须先用如下区块向用户明确汇报"
                                        f"已创建的团队（保持标题与条目内容，仅可翻译标点为用户语言，不得增删条目）：\n\n"
                                        f"## ✅ 已创建多智能体团队（可在左侧「智能体」菜单查看）\n"
                                        f"{roster_block}\n"
                                        f"\n> 这些智能体已就绪：后续可在对话中直接说「让 {coord} 处理…」来调度整个团队。\n\n"
                                        f"汇报完上述区块后，再继续委派与执行用户的原始任务。"
                                    ),
                                })
                        except Exception:
                            pass
                    # 上下文压缩：单条工具结果过长时摘要，降低单轮上下文占用
                    # （直接缓解长文档读取导致的 MAX_STEPS 耗尽）。仅当开启压缩且超阈值。
                    if (self.cfg.agent.compression_enabled
                            and self.cfg.agent.compression_tool_result_max > 0
                            and len(result) > self.cfg.agent.compression_tool_result_max):
                        try:
                            result = await CompressionContextEngine.compress_tool_result(
                                result, self.llm, self.cfg,
                                max_chars=self.cfg.agent.compression_tool_result_max)
                        except Exception:
                            pass
                    tool_total += 1
                    traj_tools.append({"name": name, "args": args, "result": result[:300]})
                    # ---- 反空转护栏（对标 Hermes IterationBudget：预算内自由运行，不硬杀任务）----
                    # 设计哲学：运行在「迭代预算」(max_steps，默认 90，对齐 Hermes 父 Agent) 内自由
                    # 推进；无论重复调用、失败循环还是确定性拦截，均**不再整体硬终止**任务——改为注入
                    # 系统提醒并计入预算，由 max_steps 统一兜底；预算耗尽前注入「收尾」提醒（grace
                    # call），确保 Agent 能给出最终结论而非半路被杀。这是对「用户实测 todo×8 被掐断 /
                    # terminal_run 确定性拦截被掐断」两类误杀的根本修正。
                    # 确定性拦截首触提醒（_blocked_nudged）见下方；此处补「连续失败 / 工具步数接近上限」
                    # 两类软提醒。纯成功重复循环的「跳过+提醒」沿用 Task #399 逻辑。
                    # —— 连续失败 / 重复失败循环：软提醒，不终止 ——
                    if _is_fail and _sig not in _fail_nudged:
                        _fail_nudged.add(_sig)
                        messages.append({
                            "role": "system",
                            "content": (
                                f"[系统] 工具 `{name}` 已连续/重复失败（累计 "
                                f"{_breaker_sig.get(_sig, 0)} 次）。请停止无意义重试："
                                f"改用其他命令/工具、修正参数，或直接向用户说明该障碍。"
                            ),
                        })
                    # —— 工具步数接近软上限：提醒收尾，不终止 ——
                    if tool_total > self.cfg.agent.max_tool_steps and _sig not in _toolcap_nudged:
                        _toolcap_nudged.add(_sig)
                        messages.append({
                            "role": "system",
                            "content": (
                                f"[系统] 工具步骤已接近上限（{self.cfg.agent.max_tool_steps}），"
                                f"请尽快停止发起新工具调用，并基于已有结果给出最终结论/交付物。"
                            ),
                        })
                    # —— 纯成功重复循环（Task #399，保留）：跳过冗余执行 + 收尾提醒，不终止 ——
                    if (not _is_fail
                            and _breaker_repeat.get(_sig, 0) >= self.cfg.agent.tool_repeat_break):
                        # 如反复 read_file 同一文档 / 反复 todo 同一清单。不再整体终止运行——
                        # 终止会把整个正常任务一并搞挂（用户实测 todo×8 即被掐断）。改为：跳过本次
                        # 冗余结果、注入「停止重复 / 进入下一步」系统提醒，让智能体改用增量处理或直接
                        # 给结论，运行继续；最终由 max_steps 迭代预算兜底，避免资源无限消耗。
                        _redirect = (
                            f"检测到重复读取/调用循环：工具 `{name}` 以完全相同参数被反复调用"
                            f"（累计 {_breaker_repeat[_sig]} 次，且均为成功返回），结果无变化。\n"
                            f"请停止重复调用：改用增量处理（分页/检索）替代整篇重读，或直接基于已得结果"
                            f"给出下一步 / 结论，不要无意义重复同一调用。"
                        )
                        if _sig not in _repeat_nudged:
                            _repeat_nudged.add(_sig)
                            messages.append({
                                "role": "system",
                                "content": f"[系统] {_redirect}",
                            })
                        # 用「跳过重复执行」提示替换本次工具结果，阻断无意义重读/重列；
                        # 不终止运行，模型可据此进入下一步。
                        result = (f"[系统] 该调用与上一次结果完全相同，已为你跳过重复执行。"
                                  f"请停止重复，按上述提示进入下一步或直接给出结论。")
                    # 所有分支均不硬终止：运行继续，由 max_steps 迭代预算统一兜底。
                    _stop = ""
                    if _stop:
                        yield {"type": "token", "text": _stop}
                        if self.memory:
                            self.memory.append(session, "assistant", _stop)
                        yield {"type": "done", "note": "anti-runaway breaker triggered"}
                        return
                    yield {"type": "tool_result", "name": name, "result": result}
                    # 会话内 nudge（对标 Hermes nudge 机制）：每完成 nudge_interval 次工具
                    # 调用，注入一条内部系统提醒，提示模型把可复用工作流沉淀为长期记忆/技能。
                    # 仅作软提示，不阻断主流程；0=关闭。
                    _ni = getattr(self.cfg.agent, "nudge_interval", 0) or 0
                    if _ni > 0 and tool_total % _ni == 0:
                        messages.append({
                            "role": "system",
                            "content": "[内部提醒] 你已连续完成多步工具调用。若过程中发现了"
                                       "可跨会话复用的用户偏好、项目事实、约定或踩坑经验，请用 "
                                       "memory 工具保存到长期记忆；若形成了一套可复用的方法论，"
                                       "可触发技能自学习将其沉淀为技能。",
                        })
                    # run.failed：工具执行失败（缺依赖/越权/异常）时静默记录，
                    # 供前端「run.failed 静默吞错检测」使用，不阻断对话主流程。
                    if result.startswith("[工具错误]") or result.startswith("[工具执行异常]"):
                        yield {"type": "run_failed", "name": name, "message": result}
                    messages.append({
                        "role": "assistant",
                        "content": choice.get("content"),
                        "tool_calls": tool_calls,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": result,
                    })
                    # 确定性拦截首触提醒：引导模型停止重试被拦命令，改用其他命令/工具，
                    # 从源头消解「重复拦截循环」（与上方 2 次硬终止形成双保险）。
                    if _is_block and _sig not in _blocked_nudged:
                        _blocked_nudged.add(_sig)
                        if _is_syntax_err:
                            _block_nudge = (
                                f"[系统] 工具 `{name}` 的本次调用被命令解析器拒绝"
                                f"（原因：{_block_reason or '未知'}）。这是**命令本身的"
                                f"语法/结构问题**（如引号未闭合），与 security.allow_shell / "
                                f"shell_allowlist / 角色权限等配置无关；以相同参数重复调用必然"
                                f"再次被拒，纯属浪费。请勿重试相同参数，请修正命令"
                                f"（例如补全引号、避免裸命令替换 `$( )`/反引号）后重试，"
                                f"或改用其他工具完成任务。"
                            )
                        else:
                            _block_nudge = (
                                f"[系统] 工具 `{name}` 的本次调用已被安全策略拦截"
                                f"（原因：{_block_reason or '未知'}）。该拦截为**确定性**结果："
                                f"以相同参数重复调用必然再次被拦，纯属浪费。请勿重试相同参数。"
                                f"若必须完成目标，请改用其他被允许的命令/工具，或向用户说明该限制"
                                f"并建议管理员调整 security.allow_shell / shell_enforce_allowlist /"
                                f" shell_allowlist 或赋予相应角色权限。"
                            )
                        messages.append({"role": "system", "content": _block_nudge})
                continue

            # ---- 文本委派兜底（弱模型兼容）：模型没发 function call，而是把
            # delegate_to_agent(agent_name="X", task="...") 当文本/代码块输出。
            # 解析后走与上方 function-call 完全相同的 _run_delegate 路径，
            # 保证子智能体真正被执行、前端能看到委派卡片与子智能体气泡。
            _content = choice.get("content") or ""
            if can_delegate and DELEGATE_TOOL_NAME in _content:
                _calls = _parse_delegate_calls(_content)
                if _calls:
                    # 模型这段「伪代码」只作为思考轨迹回填，不当最终回答吐给用户
                    messages.append({"role": "assistant", "content": _content})
                    _executed = 0
                    for _args in _calls:
                        _key = _args["agent_name"]
                        if _key in _delegated_seen:
                            messages.append({
                                "role": "user",
                                "content": f"[系统] 你已委派过 {_args['agent_name']} 且结果见上文，"
                                           f"请勿重复委派。请委派其他未执行的子智能体，"
                                           f"或直接给出最终结论。",
                            })
                            continue
                        _delegated_seen.add(_key)
                        _delegated_agents.add(_args["agent_name"])
                        _executed += 1
                        yield {"type": "tool_call", "name": DELEGATE_TOOL_NAME, "args": _args}
                        _result = ""
                        async for ev in self._run_delegate(_args, session, owner, is_admin,
                                                           user_role=user_role,
                                                           depth=delegate_depth + 1,
                                                           parent=agent_name):
                            if ev.get("type") == "delegate_end":
                                _result = ev.get("result", "")
                            yield ev
                        yield {"type": "tool_result", "name": DELEGATE_TOOL_NAME,
                               "result": _result, "agent": _args["agent_name"]}
                        tool_total += 1
                        traj_tools.append({"name": DELEGATE_TOOL_NAME, "args": _args,
                                           "result": (_result or "")[:300]})
                        # 无 tool_call_id，无法用 role=tool 回填；用系统标注的 user 消息
                        # 把子智能体结论交回主管，兼容所有 Provider。
                        messages.append({
                            "role": "user",
                            "content": (f"[系统] 子智能体 {_args['agent_name']} 已执行完毕，结果如下：\n"
                                        f"{_result or '(空)'}\n\n"
                                        f"请继续：若还有未完成的分工，输出下一个 "
                                        f"delegate_to_agent(...)；若信息已足够，请直接给出最终"
                                        f"整合结论（此时不要再输出 delegate_to_agent）。"),
                        })
                    # 连续两轮只产出重复委派 → 判定空转
                    _delegate_stall = 0 if _executed else _delegate_stall + 1
                    if _executed:
                        _did_delegate_this_step = True
                    # 覆盖度「即时补齐」：弱模型常在同一步只列出部分子智能体（典型漏派 Risk）。
                    # 协调类的一步会串行跑完所有子智能体，耗时可达数分钟；若把补齐推迟到
                    # 「收尾前覆盖度闸门」，一旦本次运行在中途被中断（客户端断连/上游超时），
                    # 缺失的子智能体就永远得不到执行（实测 delegate_end 比 delegate_start 少一个，
                    # 闸门代码从未被执行到）。故在本步内立即补齐，使覆盖度不依赖后续轮次存活。
                    if is_coordinator and _executed:
                        _gap = [a for a in _expected_cache
                                if a not in _delegated_agents and a != agent_name]
                        if _gap:
                            _delegated_agents.update(_gap)
                            _delegated_seen.update(_gap)
                            _fills: list = []
                            async for _ev in self._delegate_missing(
                                _gap, _fills, user_message=user_message, session=session,
                                owner=owner, is_admin=is_admin, user_role=user_role,
                                depth=delegate_depth + 1, parent=agent_name,
                            ):
                                yield _ev
                            for _fn, _fr in _fills:
                                tool_total += 1
                                traj_tools.append({"name": DELEGATE_TOOL_NAME,
                                                   "args": {"agent_name": _fn},
                                                   "result": (_fr or "")[:300]})
                                messages.append({
                                    "role": "user",
                                    "content": (f"[系统] 子智能体 {_fn} 已执行完毕（系统补派），"
                                                f"结果如下：\n{_fr or '(空)'}"),
                                })
                            messages.append({
                                "role": "user",
                                "content": ("[系统] 全部子智能体均已执行完毕，请直接输出最终整合结论，"
                                            "不要再输出 delegate_to_agent。"),
                            })
                    # 协调类仍有应委派子智能体未委派时，不要因「重复重派同批已派子智能体」而空转续步，
                    # 直接进入下方的覆盖度闸门（nudge / 系统兜底代委派）。否则弱模型反复重派同批会
                    # 让 _delegate_stall 永远到不了闸门，Risk 等被漏派的子智能体永远得不到委派。
                    _missing_now = [a for a in _expected_cache
                                    if a not in _delegated_agents and a != agent_name]
                    if _delegate_stall < 2 and not (is_coordinator and _missing_now):
                        continue

            # ---- 代码块兜底（弱模型兼容）：模型没发 function call，而是把 Python 写成
            # Markdown 代码块（典型如用 requests/akshare 抓取行情、做数据分析）。若直接把代码当
            # 最终回复吐给用户，就会出现「贴代码不执行」的退化表现。这里把显式 python/py 代码块
            # 抽出，走与 function call 完全一致的 code_exec 执行路径，真正运行并回填结果后 continue，
            # 让模型基于真实执行结果作答，彻底消除「只贴代码不跑」的回归。
            # 仅在当前用户被授权运行代码（operator/admin 且 allow_code_exec 开启）时生效。
            if _content and DELEGATE_TOOL_NAME not in _content:
                try:
                    from ..tools.builtins.code_exec import _code_exec_allowed
                except Exception:
                    _code_exec_allowed = None
                if _code_exec_allowed and _code_exec_allowed(self.ctx):
                    _py_blocks = _extract_python_blocks(_content)
                    if _py_blocks:
                        # 模型这段「伪代码」只作为思考轨迹回填，不当最终回答吐给用户
                        messages.append({"role": "assistant", "content": _content})
                        _code = "\n\n".join(b.strip("\n") for b in _py_blocks)
                        yield {"type": "tool_call", "name": "code_exec", "args": {"code": _code}}
                        _result = await ToolRegistry.execute(
                            "code_exec", {"code": _code}, self.ctx)
                        # 收集 /media 下载链接，供最终回复护栏兜底
                        if "/media/" in (_result or ""):
                            _ml = extract_media_links(_result)
                            if _ml:
                                _col = getattr(self, "_turn_media_links", None)
                                if _col is None:
                                    _col = []
                                    self._turn_media_links = _col
                                _col.extend(_ml)
                        tool_total += 1
                        traj_tools.append({
                            "name": "code_exec",
                            "args": {"code": _code[:120]},
                            "result": (_result or "")[:300],
                        })
                        messages.append({
                            "role": "assistant",
                            "content": _content,
                            "tool_calls": [{
                                "id": f"code_exec_{tool_total}",
                                "type": "function",
                                "function": {
                                    "name": "code_exec",
                                    "arguments": json.dumps({"code": _code}, ensure_ascii=False),
                                },
                            }],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": f"code_exec_{tool_total}",
                            "content": _result or "",
                        })
                        messages.append({
                            "role": "user",
                            "content": (
                                "[系统] 你刚才提供的 Python 代码已由系统通过 code_exec 工具实际执行，"
                                "执行结果见上方工具返回。请基于真实执行结果进行分析与总结，"
                                "直接给出最终结论，不要再重复贴出相同的代码块。"
                            ),
                        })
                        continue

            # 协调类智能体强制委派闸门：
            # 若 Orchestrator 等协调类 Agent 在第一轮没有实际发起任何委派（既没有 function call，
            # 也没有文本 delegate_to_agent/<tool_call>），则追加硬性系统提示要求其立即委派，
            # 避免其只输出计划文字就进入最终回答。
            if is_coordinator and step == 0 and not _did_delegate_this_step:
                _coordinator_nudge = (
                    "[系统强制提醒] 你是协调类智能体，当前任务是统筹分派给子智能体。"
                    "你必须立即调用 delegate_to_agent 工具把子任务委派出去，禁止只输出计划或说明文字。"
                    "若任务涉及多个子智能体，请逐个或并行发起 delegate_to_agent 调用。"
                )
                messages.append({"role": "system", "content": _coordinator_nudge})
                # 调试：记录一次协调类 Agent 进入强制委派闸门
                try:
                    from ...context import get_ctx
                    if getattr(get_ctx(), "audit", None) is not None:
                        get_ctx().audit.log(
                            self.ctx.user or "anonymous",
                            "coordinator_gate",
                            f"agent={agent_name} step={step} nudge=1",
                        )
                except Exception:
                    pass
                continue

            final = choice.get("content", "")
            # 下载链接护栏：本轮工具已产出 /media 链接、但模型未透传且搪塞时，强制补回
            _guarded = False
            _turn_links = getattr(self, "_turn_media_links", None) or []
            if _turn_links:
                final, _guarded = guard_download_links(final, _turn_links)
                if _guarded:
                    try:
                        from ...context import get_ctx
                        _gctx = get_ctx()
                        if getattr(_gctx, "audit", None) is not None:
                            _gctx.audit.log(
                                self.ctx.user or "anonymous", "download_guard",
                                f"recovered {len(_turn_links)} link(s) in final",
                            )
                    except Exception:
                        pass
            # 协调类覆盖度闸门：若尚有已知子智能体（从 prompt/registry 推断）未被委派，
            # 禁止直接收尾，强制补齐，避免弱模型「只委派部分子智能体就结束」（如漏派 Risk）。
            # 模型连续多次（≥3 次）或临近步数上限仍不补齐时，系统兜底直接代委派剩余子智能体，
            # 保证覆盖度——这是弱模型不可靠的兜底，强模型通常首轮即覆盖全部，不会触发。
            if is_coordinator:
                _expected = _expected_cache
                _missing = [a for a in _expected if a not in _delegated_agents and a != agent_name]
                if _missing:
                    # 模型本轮仍在主动委派（且尚未到尾声）：先给机会自行补齐，不干预
                    if _did_delegate_this_step and step < max_steps - 3:
                        pass
                    else:
                        _coverage_nudges += 1
                        if _coverage_nudges >= 3 or step >= max_steps - 3:
                            # 系统兜底：直接代委派剩余子智能体，保证全部覆盖
                            _delegated_agents.update(_missing)
                            _delegated_seen.update(_missing)
                            _auto: list = []
                            async for _ev in self._delegate_missing(
                                _missing, _auto, user_message=user_message, session=session,
                                owner=owner, is_admin=is_admin, user_role=user_role,
                                depth=delegate_depth + 1, parent=agent_name,
                            ):
                                yield _ev
                            for _fn, _fr in _auto:
                                tool_total += 1
                                traj_tools.append({"name": DELEGATE_TOOL_NAME,
                                                   "args": {"agent_name": _fn},
                                                   "result": (_fr or "")[:300]})
                                messages.append({
                                    "role": "user",
                                    "content": (f"[系统] 子智能体 {_fn} 已执行完毕（系统补派），"
                                                f"结果如下：\n{_fr or '(空)'}"),
                                })
                            _did_delegate_this_step = True
                            continue
                        messages.append({
                            "role": "system",
                            "content": f"[系统强制提醒] 你尚未委派给以下子智能体：{', '.join(_missing)}。"
                                       f"必须立即调用 delegate_to_agent 把它们全部委派，禁止跳过或给出最终结论。"
                                       f"（已委派：{', '.join(sorted(_delegated_agents)) or '无'}）",
                        })
                        continue

            # 主管未委派兜底闸门：存在协调类智能体、且本次为复合任务，但主管整轮都没有发起
            # 任何委派（弱模型常见：无视系统提示直接自己作答，即用户反馈的「主管没有编排委派」）
            # → 先硬提醒一次；仍不委派则系统兜底直接代委派给协调类智能体，
            # 保证「主管 → 协调者 → 执行体」链路必然成立。
            if _sup_must_delegate and not _delegated_agents:
                _target = _sup_coordinators[0]
                _sup_nudges += 1
                if _sup_nudges >= 2 or step >= max_steps - 2:
                    # 兜底前先做一次路由确认：任务确实属于该协调者职责才强制委派，
                    # 避免把无关任务（如「写个冒泡排序」）误派给投资总监这类专业协调者。
                    if not await self._confirm_route_to(user_message, _target, model):
                        _sup_must_delegate = False
                        _sup_nudges = 0
                        messages.append({
                            "role": "system",
                            "content": "[系统] 已确认该任务不属于任何协调类智能体的职责范围，"
                                       "请你直接给出完整回答。",
                        })
                        continue
                    _sargs = {"agent_name": _target, "task": (user_message or "")[:4000]}
                    _delegated_agents.add(_target)
                    yield {"type": "tool_call", "name": DELEGATE_TOOL_NAME, "args": _sargs}
                    _sr = ""
                    async for _ev in self._run_delegate(
                        _sargs, session, owner, is_admin, user_role=user_role,
                        depth=delegate_depth + 1, parent=agent_name,
                    ):
                        if _ev.get("type") == "delegate_end":
                            _sr = _ev.get("result", "")
                        yield _ev
                    yield {"type": "tool_result", "name": DELEGATE_TOOL_NAME,
                           "result": _sr, "agent": _target}
                    tool_total += 1
                    traj_tools.append({"name": DELEGATE_TOOL_NAME, "args": _sargs,
                                       "result": (_sr or "")[:300]})
                    messages.append({
                        "role": "user",
                        "content": (f"[系统] 协调类智能体 {_target} 已完成执行，结果如下：\n"
                                    f"{_sr or '(空)'}\n\n"
                                    f"请基于该结果直接给出面向用户的最终整合结论，不要再调用工具。"),
                    })
                    continue
                messages.append({
                    "role": "system",
                    "content": (f"[系统强制提醒] 该任务属于需要多角色协作的复合任务，必须整体委派给"
                                f"协调类智能体 {_target}，禁止你自行作答或自行执行研究步骤。"
                                f"请立即调用 delegate_to_agent(agent_name=\"{_target}\", task=\"<完整任务描述>\")。"),
                })
                continue

            if not final:
                # 模型未返回文本但已执行工具：基于轨迹生成可见的「完成摘要」，
                # 避免聊天窗口表现为「无结果 / 没回复」（典型场景：上传大文档→建技能后
                # 模型因上下文过满而空回复，工作其实已完成，用户却看不到任何反馈）。
                if tool_total > 0:
                    bits = [f"- {t['name']}: {t['result'][:160]}" for t in traj_tools[-3:]]
                    summary = (
                        f"已为你完成操作（本次共执行 {tool_total} 个工具步骤）：\n"
                        + "\n".join(bits)
                        + "\n\n（模型未给出文字总结，以上为工具执行轨迹摘要。）"
                    )
                    yield {"type": "token", "text": summary}
                    if self.memory:
                        self.memory.append(session, "assistant", summary)
                    if self.memory_manager is not None:
                        try:
                            await self._sync_memory(owner, "assistant", summary)
                        except Exception:
                            pass
                    yield {"type": "done"}
                    return
                yield {"type": "done", "note": "模型未返回内容"}
                return
            # 续写补全：若上一轮因达到 max_tokens 而被截断（finish_reason=="length"），
            # 追加「继续写」指令让模型接着上文输出剩余部分，最多续 3 次，彻底避免
            # 「回复内容不完整」。即使 agent.max_tokens 已调大，此兜底仍能兜住个别超长报告。
            _cont_budget = 3
            while finish_reason == "length" and _cont_budget > 0:
                _cont_budget -= 1
                messages.append({"role": "assistant", "content": final})
                messages.append({
                    "role": "user",
                    "content": "（请严格接着上文继续输出尚未写完的部分，不要重复已写内容，不要重新开头。）",
                })
                _cont_resp = await self.llm.chat(
                    messages, model=model, tools=None,
                    max_tokens=self.cfg.agent.max_tokens,
                )
                if not isinstance(_cont_resp, dict) or "choices" not in _cont_resp:
                    break
                _cont_choice = _cont_resp["choices"][0]["message"]
                final += _cont_choice.get("content", "")
                finish_reason = _cont_resp["choices"][0].get("finish_reason", "stop")
            # 续写补全后再次兜底：防止续写段又把下载链接搪塞掉
            if _turn_links:
                final, _g2 = guard_download_links(final, _turn_links)
                _guarded = _guarded or _g2
            # 下载链接护栏（续二）：剥离模型泄漏的内部绝对路径 + 搪塞话术，
            # 并尽量把真实文件重新发布为 /media 链接，确保「生成文件必有下载链接」。
            # 与原仅覆盖 SANDBOX_ROOT 的版本相比，现统一走 publish_referenced_paths：
            #   ① 落在媒体根（data_dir/generated）内的文件直接改写为 /media 链接（零拷贝）；
            #   ② 其它任意真实存在的文件（sandbox / output /tmp/挂载卷等）一并拷贝发布。
            try:
                from ..tools.builtins import SANDBOX_ROOT as _SB_ROOT
                # 延迟导入避免循环依赖：artifacts 又反向 import 本包 download_guard，
                # 而本包 __init__ 会在导入期触发 agent.py 顶层执行，顶层 import artifacts 会形成环。
                from ..tools.builtins.artifacts import publish_referenced_paths
                _media = getattr(self.ctx, "media", None)
                _owner = getattr(self.ctx, "user", "anonymous") or "anonymous"
                _recovered = []
                if _media is not None:
                    _ref_text, _recovered = publish_referenced_paths(
                        final, _media, _owner,
                        media_root=getattr(_media, "root", None),
                        sandbox_root=_SB_ROOT, out_dir=None,
                    )
                    if _recovered:
                        final = _ref_text
                final = strip_evasion(final)
                final = strip_leaked_paths(final)
                if _recovered and not _guarded:
                    final = (final + "\n\n---\n\n📎 **本次生成的可下载文件（点击即可下载）：**\n"
                             + "\n".join(f"- [{n}]({u})" for n, u in _recovered)).strip()
            except Exception:
                pass
            # 直接流式输出最终回答：self.llm.chat()（非流式）已在 step 起始处拿到完整
            # content（final），无需再调一次 self.llm.stream() 重新生成。这样既省一次推理往返，
            # 也避免「最终回答生成期间长时间无 SSE 业务事件」导致前端空闲计时器误判断流
            # （曾表现为长工具循环后「对话不出结果，需重发才出」）。
            answer = final
            # 若本轮 create_team 成功创建了团队，把醒目前缀拼到最终回复开头，确保用户一定看到
            _roster = getattr(self, "_team_roster_preface", "")
            if _roster and not answer.lstrip().startswith("## ✅ 已创建多智能体团队"):
                answer = _roster + answer
                self._team_roster_preface = ""
            yield {"type": "token", "text": answer}
            if self.memory:
                self.memory.append(session, "assistant", answer)
            if self.memory_manager is not None:
                await self._sync_memory(owner, "assistant", answer)
            # 技能自进化闭环：复杂任务（步数/工具数达标）完成后，异步由 LLM 蒸馏沉淀为技能文件。
            # 用 create_task 触发，不阻塞流式 done 事件；内部自带阈值/异常兜底。
            if self.cfg.agent.skills_auto_learn and tool_total >= self.cfg.agent.skills_auto_learn_min_tools:
                asyncio.create_task(maybe_learn(
                    self.cfg, self.llm,
                    user_message=user_message, answer=answer,
                    traj_tools=traj_tools, steps_used=step + 1,
                    tool_total=tool_total, owner=owner,
                ))
            # 后台记忆反思：每轮成功回答后（opt-in），fork 廉价 LLM 调用把可沉淀的用户事实
            # 写入长期记忆 MEMORY.md。同样用 create_task 触发，异常安全，不写脏记忆。
            if self.cfg.agent.reflection_enabled:
                asyncio.create_task(maybe_reflect(
                    self.cfg, self.llm,
                    user_message=user_message, answer=answer, owner=owner,
                ))
            yield {"type": "done"}
            return

        # 达迭代预算退出（对标 Hermes 预算耗尽：不再「已自动终止」，而是温和收尾，
        # 把已执行的轨迹摘要回给用户，任务以「已为你收尾」方式结束而非被掐断）。
        if tool_total > 0:
            bits = [f"- {t['name']}: {t['result'][:160]}" for t in traj_tools[-3:]]
            summary = (
                f"已抵达推理步数上限（{max_steps}），本次共执行 {tool_total} 个工具步骤，已为你收尾：\n"
                + "\n".join(bits)
                + "\n\n（如需继续，可补充指令让任务延续。）"
            )
            _roster = getattr(self, "_team_roster_preface", "")
            if _roster:
                summary = _roster + summary
                self._team_roster_preface = ""
            yield {"type": "token", "text": summary}
            if self.memory:
                self.memory.append(session, "assistant", summary)
        yield {"type": "done", "note": f"iteration budget reached({max_steps})"}

    # =====================================================================
    # 多模态附件组装（对标 Hermes 图片 / PDF 处理，零 OCR）
    # =====================================================================
    def _build_multimodal_content(self, user_text: str, attachments: list, model_name: str):
        """把本轮流附件组装为发给模型的 content（str 或 content 列表）。

        - 图片：image_input_mode=native 时转 base64 vision part + [Image attached at:] 提示；
                否则仅提示"模型无视觉"。
        - 扫描件 PDF（无文本层）：渲染每页为 PNG 视觉图片（不 OCR）；
        - 文本 PDF / 其他二进制文档：注入上下文提示，让 Agent 用 read_file 自取文本。
        处理失败的附件进入 skipped，一并告知模型，绝不静默丢弃。
        """
        cfg = self.cfg
        data_dir = cfg.server.data_dir
        store_dir = cfg.media.store_dir
        image_mode = image_routing.decide_image_input_mode(cfg, model_name)

        texts: list[str] = []
        if user_text:
            texts.append(user_text)

        image_parts: list[dict] = []
        notes: list[str] = []
        skipped: list[str] = []

        # ---- 图片 ----
        img_paths: list[str] = []
        for att in attachments:
            if not att.get("is_image"):
                continue
            if image_mode == "native":
                fpath = image_routing.resolve_attachment_file(att, data_dir, store_dir)
                if fpath:
                    img_paths.append(fpath)
                else:
                    skipped.append(f"{att.get('title', '图片')}: 找不到本地文件")
            else:
                notes.append(
                    f"[Note] 图片「{att.get('title', '')}」已上传，但当前模型未启用视觉能力"
                    f"（image_input_mode=text），无法分析其内容。")
        if img_paths:
            parts, sk = image_routing.build_native_content_parts(img_paths)
            image_parts.extend(parts)
            skipped.extend(f"{os.path.basename(p)}: {r}" for p, r in sk)
            if parts:
                hint = image_routing.build_image_attach_hint(img_paths)
                if hint:
                    notes.append(hint)

        # ---- PDF / 其他二进制文档 ----
        for att in attachments:
            ftype = (att.get("file_type") or "").upper()
            if att.get("is_image"):
                continue
            fpath = image_routing.resolve_attachment_file(att, data_dir, store_dir)
            if not fpath:
                skipped.append(f"{att.get('title', '文件')}: 找不到本地文件")
                continue
            if ftype == "PDF":
                if image_mode == "native" and not image_routing.pdf_has_text_layer(fpath):
                    pages, meta = image_routing.pdf_pages_to_data_urls(fpath)
                    if pages:
                        image_parts.extend(
                            {"type": "image_url", "image_url": {"url": u}} for u in pages)
                        notes.append(
                            f"[PDF attached: {att.get('title')} ({meta['pages']} page(s) "
                            f"rendered as images for vision, {meta['skipped']} skipped)] "
                            f"saved at: {fpath}")
                    else:
                        notes.append(image_routing.build_document_context_note(
                            att.get("title", "document"), fpath))
                else:
                    notes.append(image_routing.build_document_context_note(
                        att.get("title", "document"), fpath))
            else:
                notes.append(image_routing.build_document_context_note(
                    att.get("title", "document"), fpath))

        # ---- 组装最终 content ----
        if image_parts:
            content: list[dict] = []
            if texts or notes:
                content.append({"type": "text", "text": "\n".join(texts + notes)})
            content.extend(image_parts)
            if skipped:
                content.append({"type": "text",
                                 "text": "[Skipped attachments: " + "; ".join(skipped) + "]"})
            return content
        all_text = texts + notes
        if skipped:
            all_text.append("[Skipped attachments: " + "; ".join(skipped) + "]")
        return "\n".join(all_text) if all_text else (user_text or "")

    # =====================================================================
    # 长期记忆同步（向量 provider，opt-in；memory_manager 为 None 时全 no-op）
    # =====================================================================
    async def _sync_memory(self, owner: Optional[str], role: str, content: str) -> None:
        if self.memory_manager is None:
            return
        try:
            # MemoryManager.sync_turn 内部仅在存在外部 provider 时落库，否则直接返回
            await self.memory_manager.sync_turn(owner, role, content)
        except Exception:
            pass

    async def _delegate_missing(self, missing: list, results: list, *,
                                user_message: str, session: str, owner: Optional[str],
                                is_admin: bool, user_role: Optional[str],
                                depth: int, parent: Optional[str]) -> AsyncIterator[dict]:
        """系统兜底：代协调类智能体委派其漏派的子智能体，逐个执行并转发事件。

        弱模型（glm/sensenova 等）常只列出部分子智能体（典型：漏派 Risk）。本方法把
        缺失的子智能体逐个补齐，执行结果通过 ``results`` 以 ``(name, result)`` 回传，
        供调用方回填对话上下文。
        """
        for _m in missing:
            _meta = get_agent_meta(_m) or {}
            _role = _meta.get("description") or _m
            _task = (user_message or "") + (
                f"\n\n（协调类智能体未主动委派给你，系统兜底代委派："
                f"请基于你的角色「{_role}」完成你负责的部分。）"
            )
            _args = {"agent_name": _m, "task": _task[:4000]}
            yield {"type": "tool_call", "name": DELEGATE_TOOL_NAME, "args": _args}
            _result = ""
            async for _ev in self._run_delegate(_args, session, owner, is_admin,
                                                user_role=user_role,
                                                depth=depth, parent=parent):
                if _ev.get("type") == "delegate_end":
                    _result = _ev.get("result", "")
                yield _ev
            yield {"type": "tool_result", "name": DELEGATE_TOOL_NAME,
                   "result": _result, "agent": _m}
            results.append((_m, _result))

    async def _confirm_route_to(self, user_message: str, target: str, model: str) -> bool:
        """路由确认：该任务是否应交给协调类智能体 ``target``。

        仅在「主管拒不委派 → 系统兜底代委派」前调用一次，避免把与协调者职责无关的
        任务（如「写个冒泡排序」误派给投资总监）强行推给它。
        判定失败/异常时返回 True（保守选择委派），保证委派链路不被静默跳过。
        """
        try:
            meta = get_agent_meta(target) or {}
            desc = f"{meta.get('description') or ''}\n{meta.get('system_prompt') or ''}"
            resp = await self.llm.chat(
                [
                    {"role": "system",
                     "content": "你是任务路由判定器。只输出「是」或「否」两个字之一，禁止任何解释。"},
                    {"role": "user",
                     "content": (f"智能体「{target}」的职责说明：\n{desc[:1500]}\n\n"
                                 f"用户任务：{(user_message or '')[:500]}\n\n"
                                 f"该任务是否属于「{target}」的职责范围、应当交给它处理？"
                                 f"只回答 是 或 否。")},
                ],
                model=model, max_tokens=32,
            )
            txt = (resp["choices"][0]["message"].get("content") or "").strip()
            if "否" in txt or txt.lower().startswith("no"):
                return False
            return True
        except Exception:
            return True

    # =====================================================================
    # 多 Agent 委派：主管调用 delegate_to_agent 时，实时运行子智能体并转发事件
    # =====================================================================
    async def _run_delegate(self, args: dict, session: str, owner: Optional[str],
                            is_admin: bool = False,
                            user_role: Optional[str] = None,
                            depth: int = 1,
                            parent: Optional[str] = None) -> AsyncIterator[dict]:
        from ...context import get_ctx

        name = args.get("agent_name") or args.get("agent")
        task = (args.get("task") or "").strip()
        yield {"type": "delegate_start", "agent": name, "task": task}

        # 自委派防护：主管把任务派给自己会形成同名递归，深度上限之外再加一道显式拦截。
        if parent and name and str(name).strip() == str(parent).strip():
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派失败] 不能把任务委派给自己（{name}），请指定其他子智能体。"}
            return

        meta = get_agent_meta(name)
        if not meta:
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派失败] 未找到子智能体：{name}"}
            return
        # 多用户隔离：他人私有子智能体对当前用户不可见 → 视同不存在（防枚举探测）。
        if not can_view(meta.get("owner") or None, owner, is_admin, bool(meta.get("shared")),
                        meta.get("share_with") or None, user_role):
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派失败] 未找到子智能体：{name}"}
            return
        if not is_enabled(name):
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派失败] 子智能体已停用：{name}"}
            return

        g = get_ctx()
        # 委派审计（动作级）：记录谁把任务委派给了哪个子智能体（企业合规留痕）
        try:
            if getattr(g, "audit", None) is not None:
                g.audit.log(owner or "anonymous", "delegate",
                            f"target={name} task={(task or '')[:160]}")
        except Exception:
            pass
        # 子智能体使用独立 scratch 会话，避免其推理过程污染主会话历史；
        # 主管线程仅收到最终结论（delegate_end.result），主对话保持干净（P0-2 会话隔离）。
        scratch_session = f"{session}::delegate::{name}"
        sub_ctx = ToolContext(
            kb=g.kb, security=g.cfg.security, media=g.media,
            user=owner or "anonymous", session=scratch_session,
            is_admin=is_admin, user_role=user_role or "", agent_name=name,
        )
        # memory_manager=None：子智能体输出不写用户长期记忆（避免污染；主管最终回答已涵盖）
        sub = Agent(g.cfg, g.llm, g.kb, g.memory, sub_ctx, media=g.media,
                    context_engine=self.context_engine, memory_manager=None)

        # 整体超时熔断（P1-1）：用队列在后台消费子智能体事件流，主协程按「整体超时」
        # 抽取并实时转发；超时即取消消费任务并返回错误，避免子智能体卡死拖挂主 SSE 流。
        timeout = float(getattr(self.cfg.agent, "delegate_timeout", 0) or 0)
        # 嵌套委派的超时预算放大：协调类子智能体（如 Orchestrator）会在其内部再**串行**
        # 分派多个执行体，总耗时是普通子智能体的数倍。若沿用父级同一预算（默认 300s），
        # 会出现「孙级还没跑完，父级就熔断 cancel」——实测表现为 Orchestrator 只跑完
        # Research/Factor 就被中止，Strategy/Risk 永远得不到执行，且其 run 协程被取消导致
        # 覆盖度补齐逻辑根本执行不到（delegate_end 事件也随之缺失）。
        # 故按「该协调者预计要分派的子智能体数量」放大预算。
        if timeout > 0:
            _sub_tools = meta.get("tools")
            if isinstance(_sub_tools, list) and DELEGATE_TOOL_NAME in _sub_tools:
                _fanout = max(2, len(_expected_sub_agents(meta)) or 4)
                timeout *= _fanout
        collected: list[str] = []
        q: "asyncio.Queue" = asyncio.Queue()

        async def _consume():
            try:
                async for ev in sub.run(task, scratch_session, model=meta.get("model"),
                                        owner=owner, agent_name=name,
                                        is_admin=is_admin, user_role=user_role,
                                        delegate_depth=depth):
                    await q.put(ev)
            except Exception as e:  # noqa: BLE001
                await q.put({"type": "_delegate_error", "message": str(e)[:200]})
            finally:
                await q.put(None)  # 哨兵：正常结束

        cons = asyncio.create_task(_consume())
        timed_out = False
        deadline = (time.monotonic() + timeout) if timeout > 0 else None
        try:
            while True:
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                if ev is None:
                    break
                if ev.get("type") == "_delegate_error":
                    yield {"type": "delegate_end", "agent": name,
                           "result": f"[委派失败] {ev.get('message', '未知错误')}"}
                    return
                et = ev.get("type")
                if et == "token":
                    collected.append(ev["text"])
                    yield {"type": "token", "text": ev["text"], "agent": name}
                elif et in ("tool_call", "tool_result"):
                    yield {**ev, "agent": name}
                elif et in ("delegate_start", "delegate_end"):
                    # 嵌套委派（子智能体再委派给更细的执行体）：原样转发，
                    # 保留 ev["agent"]（真正的子智能体名），不覆盖为当前 name，
                    # 否则多级链路在事件流与推理图谱中会被压平/丢失。
                    yield dict(ev)
                elif et in ("error", "run_failed"):
                    # 子智能体内部错误（模型不可用/工具越权/响应畸形）必须上浮：
                    # 否则 collected 为空 → delegate_end.result 空串，主管只能靠脑补
                    # 编造子智能体结论（曾表现为「委派成功但子智能体无输出」）。
                    msg = ev.get("message") or ev.get("result") or "未知错误"
                    collected.append(f"[子智能体错误] {msg}")
                    yield {"type": "error", "message": f"[{name}] {msg}", "agent": name}
                elif et == "done" and ev.get("note"):
                    collected.append(f"[子智能体未产出内容] {ev['note']}")
        finally:
            if not cons.done():
                cons.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cons
        if timed_out:
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派超时] 子智能体 {name} 超过 {timeout:.0f}s 未完成，已中止"
                             f"（已收集 {len(collected)} 字结论）。"}
            return
        yield {"type": "delegate_end", "agent": name, "result": "".join(collected)}

    # =====================================================================
    # 图像生成分支
    # =====================================================================
    async def _run_image(self, prompt, session, pc, model, image=None) -> AsyncIterator[dict]:
        prompt = (prompt or "").strip()
        if not prompt:
            yield {"type": "error", "message": "请输入图像描述（prompt）。"}
            yield {"type": "done"}
            return
        if self.memory:
            self.memory.append(session, "user", prompt)
        mode = "图生图" if image else "文生图"
        yield {"type": "status", "text": f"正在用 {model} {mode}…"}
        try:
            res = await self.llm.generate_image(pc, model, prompt, image=image)
            url = await self._persist_media(res, kind="img", default_ext="png")
        except Exception as e:
            msg = f"图像生成失败：{self._fmt_err(e)}"
            yield {"type": "error", "message": msg}
            if self.memory:
                self.memory.append(session, "assistant", f"[图像生成失败] {msg}")
            yield {"type": "done"}
            return
        yield {"type": "image", "url": url, "prompt": prompt, "model": model}
        if self.memory:
            self.memory.append(session, "assistant", f"[图像已生成] {prompt}\n{url}")
        yield {"type": "done"}

    # =====================================================================
    # 视频生成分支（异步：提交任务 + 轮询）
    # =====================================================================
    async def _run_video(self, prompt, session, pc, model, image=None) -> AsyncIterator[dict]:
        prompt = (prompt or "").strip()
        if not prompt:
            yield {"type": "error", "message": "请输入视频描述（prompt）。"}
            yield {"type": "done"}
            return
        if self.memory:
            self.memory.append(session, "user", prompt)
        media = self.cfg.media
        mode = "图生视频" if image else "文生视频"
        yield {"type": "status", "text": f"正在用 {model} 提交{mode}任务…"}
        try:
            task_id = await self.llm.create_video_task(pc, model, prompt, image=image)
        except Exception as e:
            msg = f"视频任务提交失败：{self._fmt_err(e)}"
            yield {"type": "error", "message": msg}
            if self.memory:
                self.memory.append(session, "assistant", f"[视频生成失败] {msg}")
            yield {"type": "done"}
            return

        deadline = time.time() + media.video_timeout
        video_url = None
        last_progress = -1
        while time.time() < deadline:
            await asyncio.sleep(media.video_poll_interval)
            try:
                st = await self.llm.poll_video_once(pc, task_id)
            except Exception as e:
                yield {"type": "status", "text": f"轮询中（网络波动，重试）… {self._fmt_err(e)}"}
                continue
            status = st.get("status")
            prog = st.get("progress")
            if isinstance(prog, (int, float)) and prog != last_progress:
                last_progress = prog
                yield {"type": "status", "text": f"视频生成中… {int(prog)}%"}
            elif status:
                yield {"type": "status", "text": f"视频生成中…（{status}）"}
            if status in ("completed", "succeeded", "success"):
                video_url = st.get("video_url")
                break
            if status in ("failed", "error", "cancelled"):
                msg = f"视频生成失败：{st.get('error') or status}"
                yield {"type": "error", "message": msg}
                if self.memory:
                    self.memory.append(session, "assistant", f"[视频生成失败] {msg}")
                yield {"type": "done"}
                return

        if not video_url:
            msg = "视频生成超时，请稍后在提供商侧查询任务，或重试。"
            yield {"type": "error", "message": msg}
            if self.memory:
                self.memory.append(session, "assistant", f"[视频生成超时] {msg}")
            yield {"type": "done"}
            return

        yield {"type": "status", "text": "正在下载并保存视频…"}
        try:
            url = await self._persist_media({"url": video_url}, kind="vid", default_ext="mp4")
        except Exception:
            url = video_url
        yield {"type": "video", "url": url, "prompt": prompt, "model": model}
        if self.memory:
            self.memory.append(session, "assistant", f"[视频已生成] {prompt}\n{url}")
        yield {"type": "done"}

    # =====================================================================
    # 工具方法
    # =====================================================================
    @staticmethod
    def _first_model_of_kind(pc, kind: str) -> Optional[str]:
        for m in (pc.models or []):
            if classify_model(m) == kind:
                return m
        return None

    def _pick_img2img_model(self, pc, model: str) -> str:
        low = (model or "").lower()
        if "2.1" not in low:
            return model
        for m in (pc.models or []):
            ml = m.lower()
            if classify_model(m) == "image" and ("2.0" in ml or "edit" in ml):
                return m
        return model

    async def _persist_media(self, res: dict, kind: str, default_ext: str) -> str:
        if res.get("b64"):
            import base64

            data = base64.b64decode(res["b64"])
            if self.media:
                return self.media.save_bytes(data, kind=kind, ext=default_ext, owner=self.ctx.user)
            return "data:image/png;base64," + res["b64"]
        url = res.get("url")
        if not url:
            raise RuntimeError("生成结果为空")
        if not self.media:
            return url
        data = await self.llm.download(url)
        ext = type(self.media).guess_ext(url, fallback=default_ext)
        return self.media.save_bytes(data, kind=kind, ext=ext, owner=self.ctx.user)

    @staticmethod
    def _fmt_err(e: Exception) -> str:
        import httpx

        if isinstance(e, httpx.HTTPStatusError):
            body = ""
            try:
                body = e.response.text[:200]
            except Exception:
                pass
            return f"HTTP {e.response.status_code} {body}".strip()
        return str(e)[:200] or e.__class__.__name__
