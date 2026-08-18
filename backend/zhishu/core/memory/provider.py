"""智枢智能体 —— 记忆 Provider 抽象（对标 Hermes `agent/memory_provider.py`）。

  记忆后端可插拔：内置 provider（SQLite 会话流水）始终第一；至多可挂一个外部
  provider（如向量抽取式长期记忆）。Agent 在每轮通过 manager 取回「记忆上下文」
  注入系统提示的 volatile 部分，并在每轮后把对话同步进记忆。

  本文件把 Hermes 的完整 MemoryProvider 契约移植到智枢（多用户 owner + 会话
  session_id 双维度），并新增：
    * TRIVIAL_PROMPT_RE / is_trivial_prompt() —— 寒暄/占位门控，跳过无语义信号的轮次；
    * set_llm(async_callable, loop) —— 注入可供抽取/改写使用的 LLM 调用（默认 no-op）。

  与 Hermes 的差异：
    * 方法签名统一携带 owner（用户归属）与 session_id（会话），适配智枢多租户隔离；
    * sync_turn 保持「同步」语义（由 MemoryManager 在后台线程/事件中非阻塞调度），
      不再要求协程——以适配 Hermes 风格的串行后台写入。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = __import__("logging").getLogger("zhishu.memory")


# Prompts that carry no semantic signal — trivial acknowledgements, greetings,
# slash commands, empty input. Single source of truth shared by the manager's
# per-turn prefetch gate (manager.prefetch_all) and provider-side classifiers.
# The alternation is anchored and may only be followed by whitespace or
# punctuation, so words that merely START with a trivial word ("k8s", "yolo",
# "note") do NOT match, while trailing-punctuation variants ("hi!", "hey.",
# "thanks :)") do. 直接沿用 Hermes 的正则，保证行为一致。
TRIVIAL_PROMPT_RE = re.compile(
    r'^(yes|no|ok|okay|sure|thanks|thank you|y|n|yep|nope|yeah|nah|'
    r'hi|hey|hello|yo|sup|'
    r'continue|go ahead|do it|proceed|got it|cool|nice|great|done|next|lgtm|k|'
    r'好的|好|谢谢|感谢|多谢|继续|收到|嗯|嗯嗯|可以|行|行吧|辛苦了|赞|棒|对|是的|'
    r'没错|了解|明白|懂了|好哒|好嘞|好滴|好的呢|知道了|晓得|妥|妥了|没问题|收到收到)'
    r'[\s!?.:;,"\'~\u2018\u2019\u201c\u201d\u2014\u2013\u2026()\[\]{}<>*&^%$#@!+=`\u00a0'
    r'，。！？、；：""''（）「」『』【】《》…—～·\uff0c\uff01\uff1f\uff1a\uff08\uff09]*$',
    re.IGNORECASE,
)


def is_trivial_prompt(text: Optional[str]) -> bool:
    """Return True if a user prompt is too trivial to warrant memory recall.

    空/纯空白输入、斜杠命令、以及仅有尾标点变体的寒暄/应答（"hi!"、"thanks :)"、
    "done???"）都算 trivial。调用方据此在「无语义信号」的轮次跳过记忆召回，
    避免一次阻塞性检索把陈旧的记忆上下文灌进单字回复里、带偏模型。
    """
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith("/"):
        return True
    return bool(TRIVIAL_PROMPT_RE.match(stripped))


class MemoryProvider(ABC):
    """记忆 Provider 接口（方法均有默认空实现，子类按需覆盖）。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """该 provider 的短标识（'builtin' / 'vector' / 'mem0' ...）。"""

    # -- 核心生命周期（必须实现） -------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """该 provider 是否已配置、具备依赖并可用。

        Agent 初始化时调用，用于决定是否激活该 provider。不得发起网络调用，
        仅检查配置与已安装依赖。
        """

    def initialize(self, session_id: str = "", **kwargs) -> None:
        """为一个会话初始化 provider（同步）。

        在 agent 启动时调用一次。可建立连接、创建资源、启动后台线程等。
        kwargs 可能包含 hermes_home 等（智枢忽略未用字段）。
        """

    def system_prompt_block(self, owner: Optional[str] = None) -> str:
        """返回需注入系统提示的静态文本（volatile 部分的额外说明）。无则空串。"""
        return ""

    def prefetch(self, query: str, *, owner: Optional[str] = None,
                 session_id: str = "") -> str:
        """召回与即将到来的轮次相关的记忆文本。

        每轮 API 调用前调用，返回注入用的格式化文本，无相关内容返回空串。
        实现应足够快——真正的召回可用后台线程，这里返回缓存结果。
        """
        return ""

    def queue_prefetch(self, query: str, *, owner: Optional[str] = None,
                       session_id: str = "") -> None:
        """为下一轮排队一次后台召回（默认 no-op）。"""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str = "",
        *,
        owner: Optional[str] = None,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """把一轮完成的对话持久化进后端（同步，建议非阻塞）。

        每轮后调用。若后端有延迟，应在内部排队后台处理，勿在此处阻塞。
        messages 为该轮完成时的 OpenAI 风格消息列表（含 tool 调用与结果），
        不需要原始轮次上下文的 provider 可忽略。
        """

    @abstractmethod
    def get_tool_schemas(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回该 provider 暴露给 Agent 的工具声明（OpenAI function 格式）。

        形如 {"name": "...", "description": "...", "parameters": {...}}。
        无工具则空列表（仅上下文）。
        """

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """处理该 provider 的某个工具调用，返回 JSON 字符串结果。

        仅对 get_tool_schemas() 返回的工具名调用。
        """
        raise NotImplementedError(f"Provider {self.name} 不处理工具 {tool_name}")

    def shutdown(self) -> None:
        """优雅停机——刷新队列、关闭连接。"""

    # -- 可选钩子（按需覆盖） -----------------------------------------------

    def set_llm(self, async_llm_callable, loop=None) -> None:
        """注入 async callable(messages, model=None) -> str，供抽取/改写使用。

        默认 no-op；需要 LLM 的 provider（抽取式记忆）覆盖本方法缓存 callable
        与事件循环引用。
        """

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """每轮开始时通知 provider（带运行时上下文）。用于计轮/维护。"""

    def on_session_end(self, messages: List[Dict[str, Any]], *,
                       owner: Optional[str] = None, session_id: str = "") -> None:
        """会话结束时通知 provider（显式退出/超时/删除）。

        用于端到端事实抽取、摘要等。messages 为完整对话历史。
        不会每轮调用——仅在真实会话边界触发。
        """

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        owner: Optional[str] = None,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Agent 中途切换 session_id 时通知 provider。

        用于 /reset、/new、上下文压缩等需要重新绑定会话状态的路径。
        """

    def on_pre_compress(self, messages: List[Dict[str, Any]], *,
                        owner: Optional[str] = None, session_id: str = "") -> str:
        """上下文压缩丢弃旧消息前调用，返回要纳入压缩摘要的抽取文本。

        实现可用它把「即将被压缩的消息」里的事实抽取出来，返回文本注入压缩
        摘要提示，使压缩器保留这些洞察。默认空串（向后兼容）。
        """
        return ""

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """父 Agent 在子智能体完成时收到 (task, result) 观察。"""

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """内置记忆工具写入时通知外部 provider，用于镜像写操作。"""

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """返回该 provider 配置所需的字段（用于设置 UI）。默认空（无需配置）。"""
        return []

    def save_config(self, values: Dict[str, Any], data_dir: str) -> None:
        """把非机密配置写入 provider 原生位置（无原生配置文件的保持 no-op）。"""

    def backup_paths(self) -> List[str]:
        """返回 provider 在 data_dir 之外存储的额外路径（用于备份）。默认空。"""
        return []
