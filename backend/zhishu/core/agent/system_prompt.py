"""智枢智能体 —— 系统提示分层组装（对标 Hermes `agent/system_prompt.py`）。

  把系统提示拆成两层拼接：
    * stable（稳定）  —— 身份 / 全局指令 / 工具指引，变化频率低。
    * volatile（易变）—— 技能指令、长期记忆(USER/SOUL/MEMORY)、知识库检索结果、
                        时间戳，每轮可能不同，便于刷新与上下文管理。
"""
from __future__ import annotations

from typing import Optional

from ..config import ZhishuConfig
from ..rag import KnowledgeBase
from ..agents_runtime import build_agent_system_prompt, list_agents
from ..modules import build_agent_context_prompt, build_user_memory_prompt

# 工具使用指引（主管模式注入，帮助模型聚焦稳定可用的解析路径，避免调用
# 已废弃或易失败的旧插件导致空转/死循环）。
_TOOL_GUIDANCE = """\
【文档与图片解析指引】
- 读取用户上传的文档（txt/md/csv/tsv/json/代码/日志等文本，以及 docx/xlsx/pptx/odt·ods·odp/rtf/epub/pdf 等）：\
一律使用 read_file 工具，传入附件的 stored_path（或 /media/ URL），它基于标准库零依赖解析，\
并支持分页(page)、行号(start_line/end_line)、字符预算(max_chars)。
- parse_docx / parse_xlsx / parse_pdf 等旧插件已废弃，不要再调用，以免失败或空转。
- 图片：作为视觉参考直接传给模型即可（系统不内置 OCR，无法提取图片内文字）。
- 自愈：若 read_file 或解析器遇到不支持的文件格式、编码、或需要标准工具没有的处理\
逻辑而失败，可用 code_exec 编写 Python 直接读取并处理该文件（把 stored_path 作为 \
path 参数传入，代码中用 os.environ['TARGET_FILE'] 读取，结果 print 到 stdout）。\
若同一种处理逻辑需反复使用，可改用 create_tool 将其注册为可复用工具（dyn_ 前缀）。\
注意：这是模型自生成的代码，仅在内网可信部署下使用，且网络已被禁用。

【任务规划与回忆指引】
- 复杂任务（3 步以上）：先用 todo 工具写入拆解清单（[{id, content, status}]），\
每完成一项立即更新状态，避免长对话跑偏或遗漏步骤。
- 用户提到「之前聊过 / 上次说的 / 帮我回忆」等跨会话内容：用 session_search 工具\
回忆历史会话（传 query 检索、传 session_id 翻阅、不传参数浏览最近会话）。
- 发现值得长期记住的信息（用户偏好、项目背景、约定、踩坑经验）：用 memory 工具\
的 save 动作写入长期记忆；需要修正用户画像时用 update_user；不再需要的用 forget。\
这些记忆下一轮会自动注入系统提示，让对话具备连续性。
- 需要最新外部信息且出网已放行：先用 web_search 检索线索，再用 safe_web_fetch 抓取正文。

【硬性规则 — 处理用户附件时务必遵守】
1. 处理用户附件时，必须先用 read_file 读取 stored_path 指向的文件，严禁仅凭文件名猜测文件格式或内容。
2. 严禁在未真实读取到文件内容的情况下，向用户编造「该文件是二进制 / ZIP / 无法解析」等结论。
3. 若 read_file 返回解析失败或越权，必须立即用 code_exec 编写 Python 自救（用 os.environ['TARGET_FILE'] 读取磁盘文件），严禁直接回复「无法解析，请重新上传格式正确的文件」。
4. 若文件实为压缩包，read_file 会自动解包并返回内部文本；若仍拿不到内容，用 code_exec 进一步处理内部条目。"""


def _delegate_catalogue(owner: Optional[str], is_admin: bool,
                      user_role: Optional[str]) -> tuple[str, bool]:
    """生成主管模式下可见的「可委派智能体」清单并注入路由原则。

    返回 (prompt_text, has_coordinator)。其中 has_coordinator 表示是否存在
    显式携带 delegate_to_agent 的协调类智能体（如 Orchestrator），供外层
    判断是否需要对主管工具集做裁剪，强制走委派链路。

    让主管（agent_name=None）在对话中认识具名智能体（如 Orchestrator），
    从而把复合任务委派给协调类智能体，再由它内部分派给执行体，形成
    主管 → 协调者 → 执行体 的多级协作链路。"""
    try:
        agents = list_agents(username=owner, is_admin=is_admin, user_role=user_role)
    except Exception:
        agents = []
    agents = [a for a in agents if a.get("enabled")]
    has_coordinator = False
    lines = ["【可委派智能体（用 delegate_to_agent 协同处理专业任务）】"]
    for a in agents:
        name = a.get("name", "")
        desc = (a.get("description") or "").strip()
        tools = a.get("tools", "all")
        # 仅在工具被显式声明含委派时标记为协调类（tools="all" 视为通用执行体，不标记）
        coord = isinstance(tools, list) and "delegate_to_agent" in tools
        if coord:
            has_coordinator = True
        tag = " 〔可再调度子智能体〕" if coord else ""
        lines.append(f"- {name}{tag}：{desc}" if desc else f"- {name}{tag}")
    lines.append("")
    lines.append("【委派路由原则 —— 必须遵守】")
    # —— 任务复杂度自检：让主管真正按「输入的问题」判断是否进入多 Agent 协作 ——
    lines.append("- 收到用户消息后，先做一句话自检：本任务是『单一简单任务』还是『需要多角色 / 多步骤"
                 "协作的专业任务』？据此决定走哪条路径，严禁无差别地调用 create_team / delegate_to_agent。")
    lines.append("- 路径 A · 单一简单任务（如：问候闲聊、写诗/文案/邮件、解释概念、翻译改写、"
                 "单一事实问答、简单计算、读取单个文件等）：你自己直接回答即可，"
                 "【严禁】调用 delegate_to_agent，更【严禁】无谓地调用 create_team 组建团队。")
    lines.append("- 路径 B · 用户**显式点名某团队**（如「股票分析团队」「业务分析团队」「用股票分析团队」）"
                 "或**显式要求多智能体协作 / 多模型并行**时，才进入下方的「委派 / 建团」流程。")
    lines.append("- 重要：消息中**顺带出现**领域词（如「股票分析」「风险评估」）但用户只是在询问 / 咨询 /"
                 "闲聊（例如「除了股票分析你还能做什么」「帮我分析下这段代码」「研究一下这个想法」），"
                 "仍属于路径 A，直接作答，严禁调用 delegate_to_agent / create_team。")
    lines.append("- 你是主管，负责『决策与编排』，不直接执行需要专业工具的研究/分析/计算细节。")
    if has_coordinator:
        lines.append("- 对属于路径 B（用户已显式点名团队 / 要求多智能体协作）的任务，必须整体委派给"
                     "带「可再调度」标记的协调类智能体（如 Orchestrator 投资总监），由其内部分派给执行智能体。")
        lines.append("- 铁律：当任务属于路径 B 时，你的第一次模型回复必须伴随至少一个 delegate_to_agent 调用；"
                     "禁止只输出计划/说明文字而不实际调用工具。路径 A 的简单任务不受此限，直接作答。")
        lines.append("- 若你在本次会话中调用 create_team 成功组建了团队，必须在面向用户的回复**开头**用醒目区块"
                     "汇报团队组成（协调者与各成员及其职责），并提示用户可在左侧「智能体」菜单查看这些智能体；"
                     "之后再继续委派与执行任务。")
    else:
        lines.append("- 当前没有可用的协调类智能体。若用户任务属于路径 B（需要多角色 / 多步骤协作，"
                     "如组建分析师团队、研究项目），或用户**明确要求创建子智能体**，"
                     "请调用 create_team 工具动态组建「1 个协调者 + N 个执行成员」的团队，"
                     "创建完成后再将用户任务整体委派给协调者，由其内部分派并汇总。")
        lines.append("- 注意：不要把路径 A 的简单任务误判为需要建团。仅当用户任务天然需要多角色分工、"
                     "或显式要求「创建团队 / 建一个 agent」时，才调用 create_team。")
    lines.append("- 简单单一任务始终可由你直接回答，无需委派，也无需组建团队。")
    return "\n".join(lines), has_coordinator


def build_system_prompt(
    cfg: ZhishuConfig,
    *,
    agent_name: Optional[str] = None,
    owner: Optional[str] = None,
    kb: Optional[KnowledgeBase] = None,
    query: Optional[str] = None,
    is_admin: bool = False,
    user_role: Optional[str] = None,
) -> tuple[str, bool]:
    """组装系统提示。

    agent_name 非空 → 子智能体模式：以独立人设为 stable，并**继承用户知识库(RAG)与
    长期记忆**（增强专业性、避免重复询问已有偏好），但**不注入全局技能指令**，以免
    干扰其专属人设与职责聚焦。
    否则 → 主管模式：stable(全局人设) + volatile(技能/记忆/知识库检索)。
    """
    if agent_name:
        system = build_agent_system_prompt(agent_name)
        system = system or cfg.system_prompt
        # 继承用户知识库检索上下文（按 owner 隔离，防跨用户泄露）
        extras: list[str] = []
        if kb and query:
            ctx_text = kb.build_context(query, top_k=5, owner=owner)
            if ctx_text:
                extras.append("【内部知识库检索结果，仅用于辅助回答】\n" + ctx_text)
        # 继承用户长期记忆（MEMORY/USER/SOUL.md，按 owner 隔离）
        if owner:
            mem_ctx = build_user_memory_prompt(cfg, owner)
            if mem_ctx:
                extras.append(mem_ctx)
        if extras:
            system += "\n\n" + "\n\n".join(extras)
        return system, False

    stable = cfg.system_prompt

    volatile: list[str] = []
    # 已启用技能 + 长期记忆（MEMORY/USER/SOUL.md，均按 owner 用户隔离）
    extra = build_agent_context_prompt(cfg, owner=owner, is_admin=is_admin, user_role=user_role)
    if extra:
        volatile.append(extra)
    # 知识库检索增强
    if kb and query:
        ctx_text = kb.build_context(query, top_k=5, owner=owner)
        if ctx_text:
            volatile.append("【内部知识库检索结果，仅用于辅助回答】\n" + ctx_text)

    system = stable
    if volatile:
        system += "\n\n" + "\n\n".join(volatile)
    system += "\n\n" + _TOOL_GUIDANCE
    # 主管模式：注入可见智能体清单 + 委派路由原则，使其能自动编排委派给协调类智能体
    cat, has_coordinator = _delegate_catalogue(owner, is_admin, user_role)
    if cat:
        system += "\n\n" + cat
    return system, has_coordinator
