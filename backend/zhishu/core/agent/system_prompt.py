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
- **比对两个文件（如「对比这两份 Excel / 找出差异」）**：直接调用 compare_files 工具，传入两个文件的\
引用（stored_path / /media/ URL / 文件名）即可，对比在服务端完成并返回差异报告；\
千万不要自己 read_file 两份再人工对比，那既容易空转也不可靠。
- parse_docx / parse_xlsx / parse_pdf 等旧插件已废弃，不要再调用，以免失败或空转。
- 图片：作为视觉参考直接传给模型即可（系统不内置 OCR，无法提取图片内文字）。
- 自愈：若 read_file 或解析器遇到不支持的文件格式、编码、或需要标准工具没有的处理\
逻辑而失败，可用 code_exec 编写 Python 直接读取并处理该文件（把 stored_path 作为 \
path 参数传入，代码中用 os.environ['TARGET_FILE'] 读取，结果 print 到 stdout）。\
**更广泛的「遇到不会的任务就自己造工具」原则见下方【自扩展工具】专节**，不要只会空转或回复「无法解析」。

【减少往返 · 批量调用（提速关键）】
- 处理文件/数据时常需多次调用同类工具（如连续 read_file 多个文件、连续 terminal_run 多条命令、连续 code_exec 多段脚本、连续 generate_excel 多个工作簿）。**每一轮工具调用都伴随一次 LLM 往返与一次执行开销，串行多次调用是「智枢比 Hermes 慢」的主因。**
- **合并同类调用为一次**：这些工具都已支持批量参数，请优先使用，把原本 N 次往返压缩为 1 次：
  * read_file：用 `paths: [路径1, 路径2, ...]` 一次读取多个文件（多文件结果带 `===== 文件 xxx =====` 分隔头）；
  * terminal_run：用 `commands: [命令1, 命令2, ...]` 一次跑完多条命令（每条仍逐条过白名单/高危校验，再合并为单次 shell 执行）；
  * code_exec：用 `snippets: [代码段1, 代码段2, ...]` 一次提交多段 Python，系统在单个子进程内顺序执行（共享解释器与变量、**避免每段都冷启动一个 Python**）；
  * generate_excel：用 `files: [{filename, sheets}, ...]` 一次生成多个独立 Excel 工作簿，各自返回 /media 链接。
- 仅在确需「上一步结果决定下一步」的强依赖场景才分多次调用；相互独立的检查/读取/生成请尽量合并到一次调用里。

【自扩展工具 —— 内置没有就自己造（核心智能行为）】
- **核心原则**：遇到没有现成内置工具能完成的任务（自定义/冷门文件或二进制解析、特定网站或 API 调用、
  小众格式转换、批量处理、专属计算/校验/清洗逻辑等），**绝不要回复「做不到 / 无法处理 / 没有这个能力」**，
  而是用 Python 自己造工具把任务跑通。这是你区别于普通问答机器人的关键能力。
- **两步法**：
  1) 先用 code_exec 把 Python 一次性跑通：支持 path 参数（会把文件绝对路径注入环境变量 \
  TARGET_FILE，代码内用 os.environ['TARGET_FILE'] 读取）；结果 print 到 stdout 即可。
  2) 若同一种处理逻辑**以后还会用到**、或**用户可能反复调用**、或它是**多步骤任务中稳定的子能力**，
     用 create_tool 把它注册成可复用工具（工具名自动加 `dyn_` 前缀），**后续步骤直接以 `dyn_<name>` 调用**。
- **create_tool 参数**：`name`（工具名，2-31 字符，会加 dyn_ 前缀）、`description`（用途，帮助模型决定是否调用）、
  `code`（Python 源码，结果 print 到 stdout）、`parameters`（可选 JSON Schema 参数声明）。
  **调用该动态工具时，你传入的参数会以 JSON 字符串注入环境变量 `TOOL_ARGS_JSON`，
  代码中用 `json.loads(os.environ['TOOL_ARGS_JSON'])` 读取；结果必须 print 到 stdout。**
- **动态工具最小可运行示例**（即 create_tool 的 `code` 字段内容）：
    import os, json
    args = json.loads(os.environ.get("TOOL_ARGS_JSON", "{}"))
    # 用 args 做计算 / 解析 / 请求 ...
    print(json.dumps({"result": ...}, ensure_ascii=False))   # 结果须 print 到 stdout
- **网络**：code_exec / dyn_ 工具默认可出网（由 security.code_exec_network_isolated 控制，默认 False 不隔离），
  需要联网抓数据 / 调 API 可直接做，不要假设网络不可用。
- **决策边界**：临时一次性任务用 code_exec 即可；仅当逻辑会在会话内反复使用才用 create_tool。
  动态工具为进程级、**会话内可反复调用**，进程重启后清空（正常）；数量上限 64，超出自动淘汰最旧的一个，无需手动清理。
- 文件解析类自救（read_file 失败 / 不支持的格式）同样适用本原则：先用 code_exec 直读，
  若此类文件会反复出现再用 create_tool 沉淀为稳定解析工具（详见下方「硬性规则」第 3 条）。

【代码执行硬性规则 — 必须调用工具，严禁只贴代码】
1. 凡需要真正运行 Python（抓数据、算指标、跑分析、处理文件等），**必须调用 code_exec 工具**并传入 \
code 参数（完整可运行的 Python 源码），由系统在隔离执行环境中真实运行并回填 stdout；\
**严禁把 Python 写成 Markdown 代码块直接作为回复抛出**——那样代码不会被执行，用户只看到源码却拿不到结果。
2. 当你在回复里写 ```python ... ``` 代码块时，应先确认它已通过 code_exec 真正执行；\
若只是示例/说明用途（不打算执行），不要用 python/py 标注，避免被系统误执行。
3. code_exec 返回的结果（stdout）即是真实运行产物，请基于它给出分析与结论，不要重复贴同样的代码。

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
4. 若文件实为压缩包，read_file 会自动解包并返回内部文本；若仍拿不到内容，用 code_exec 进一步处理内部条目。

【文件产物下载指引 —— 必须严格遵守】
- 你产出的任何文件（CSV / Excel / 报告 / 图片 / 脚本等）都必须以 **/media/... 可点击下载链接** 的形式交付给用户。
- **生成 Excel(.xlsx) 必须用 generate_excel 工具**（传入表头+行，或多工作表，或从 CSV 生成）。严禁用 file_write 写 .xlsx、严禁手写 xlsx 字节/zip——那会生成 Excel 打不开的损坏文件。
- 生成 Word(.docx)/PDF 等二进制文件，请用 code_exec 编写 Python 生成（如 python-docx / fpdf），生成物会自动发布为 /media 链接；不要用 file_write 写这些扩展名（file_write 会直接拒绝并提示正确做法）。
- 纯文本文件（txt / md / csv / json / 代码 / 日志等）才用 file_write 工具：它【自动】把文件落盘到媒体库并直接返回 /media/... 下载链接。code_exec / terminal_run 产生的文件也【自动】发布为 /media/... 链接。你只需把工具结果里的链接原样透传给用户，无需、也不能关闭下载。
- 【分析报告 / 总结必须落盘为文件】当用户要求「生成报告」「出一份分析」「总结成文档」「整理结果」时，**必须用 file_write 把完整报告落盘为 .txt/.md 文件并返回 /media 链接**，对话里只给摘要 + 下载链接；不得把长篇报告全文直接写在对话回复里而不给下载链接——那等于用户「拿不到文件」。哪怕用户没明说「下载」，只要产出是一份可独立保存的报告/总结，就应当落盘给链接。
- 若用户拿不到某文件，立即用 make_downloadable 工具把该文件转成 /media 链接再交付。**make_downloadable 只能传入已真正落盘的文件名或 /media 链接；严禁凭记忆/想象拼凑一个 /media 路径当作链接交给用户**——make_downloadable 会校验文件是否真实存在，不存在的链接会被拒绝。若文件从未落盘，请先用 file_write 生成。
- 【关键认知】本系统为内网 / 本地一体化部署，用户浏览器与系统同源：工具返回的 /media/... 链接在用户端**就是可点击下载的链接**。不存在「公网 / 外网可访问链接」的概念，无需区分内外网，请勿把「下载链接」误读为「外网可访问链接」而搪塞用户。
- 【严禁编造话术】不得声称「无法生成下载链接」「请联系管理员获取」「只能给出路径」「不支持生成下载链接」「文件无法下载」等；本系统始终能生成同源可下载链接。
- 【严禁环境限制说明】不要生成任何以「当前环境限制说明」「无 Web 服务器」「无文件下载服务」「内网隔离」「无法生成可访问的下载 URL」等标题或表格形式推脱下载责任的文字；本系统始终能生成 /media/... 同源下载链接，不存在无法交付文件的情况。
- 【严禁泄露内部路径】绝不在回复中写出任何内部文件系统绝对路径（如 /app/...、/data/...、backend/data/... 等）。文件一律用 /media/... 链接交付，内部路径对用户不可见也不可用。"""


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
