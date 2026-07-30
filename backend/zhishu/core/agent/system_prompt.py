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
from ..agents_runtime import build_agent_system_prompt
from ..modules import build_agent_context_prompt

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


def build_system_prompt(
    cfg: ZhishuConfig,
    *,
    agent_name: Optional[str] = None,
    owner: Optional[str] = None,
    kb: Optional[KnowledgeBase] = None,
    query: Optional[str] = None,
    is_admin: bool = False,
    user_role: Optional[str] = None,
) -> str:
    """组装系统提示。

    agent_name 非空 → 子智能体模式：仅用其独立人设（不注入全局技能/记忆）。
    否则 → 主管模式：stable(全局人设) + volatile(技能/记忆/知识库检索)。
    """
    if agent_name:
        system = build_agent_system_prompt(agent_name)
        return system or cfg.system_prompt

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
    return system
