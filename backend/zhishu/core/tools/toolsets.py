"""智枢智能体 —— 命名工具分组（对标 Hermes `toolsets.py`）。

  每个分组是一组 tool 名 / 子分组名；check_fn 为环境探针，返回 False 时该分组
  不被暴露。子智能体 agent.json 的 tools 字段可直接引用分组名（如 "knowledge"）。
"""
from __future__ import annotations

from .base import Toolset

TOOLSETS: dict[str, Toolset] = {
    "core": Toolset(
        name="core",
        description="核心内置工具全集",
        tools=[
            "terminal_run", "file_read", "file_write", "file_list",
            "safe_web_fetch", "web_search", "knowledge_search",
            "knowledge_list", "knowledge_read", "delegate_to_agent",
            "session_search", "todo", "memory",
        ],
    ),
    "files": Toolset(
        name="files",
        description="沙箱文件系统工具",
        tools=["file_read", "file_write", "file_list"],
    ),
    "web": Toolset(
        name="web",
        description="受控外网访问（需安全策略放行）",
        tools=["safe_web_fetch", "web_search"],
        # 实际出网闸门在 ToolRegistry.execute 中按 security.outbound_allow 校验；
        # 此处探针恒为 True，避免无 ctx 时无法判定。
        check_fn=lambda: True,
    ),
    "knowledge": Toolset(
        name="knowledge",
        description="知识库检索 / 文档读取",
        tools=["knowledge_search", "knowledge_list", "knowledge_read"],
    ),
    "agent": Toolset(
        name="agent",
        description="多 Agent 委派工具（仅主管可用）",
        tools=["delegate_to_agent"],
    ),
    "skills": Toolset(
        name="skills",
        description="技能渐进披露工具（按需读取 SKILL.md 全文）",
        tools=["read_skill"],
    ),
    "sessions": Toolset(
        name="sessions",
        description="跨会话回忆（检索/翻阅/浏览历史对话）",
        tools=["session_search"],
    ),
    "todo": Toolset(
        name="todo",
        description="任务清单（复杂任务拆解与进度跟踪）",
        tools=["todo"],
    ),
    "memory": Toolset(
        name="memory",
        description="长期记忆读写（跨会话沉淀用户偏好/项目事实/约定）",
        tools=["memory"],
    ),
}
