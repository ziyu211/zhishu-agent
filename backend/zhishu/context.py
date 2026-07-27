"""智枢智能体 —— 应用上下文（单例依赖注入）。

由 main.py 启动时构建一次，供各 API 路由共享：
LLM 客户端、知识库、记忆、鉴权、审计、工具上下文。
"""
from __future__ import annotations

import os

from .core.config import ZhishuConfig
from .core.llm import LLMClient
from .core.rag import KnowledgeBase
from .core.memory import MemoryStore, MemoryManager
from .core.security import AuthService, AuditLog, UserStore, Crypto
from .core.redact import Redactor, set_default as set_default_redactor
from .core.credentials import ProviderStore, CredentialPool
from .core.conversations import ConversationStore
from .core.tools import ToolContext
from .core.media import MediaStore
from .core.modules import ModuleIntegrator
from .core.agent import Agent, build_context_engine
from .core.cron import CronScheduler


class AppContext:
    def __init__(self, cfg: ZhishuConfig):
        self.cfg = cfg
        self.llm = LLMClient(cfg)
        os.makedirs(cfg.server.data_dir, exist_ok=True)
        # 多用户存储（首个 admin 由配置引导）
        crypto = Crypto(cfg.security.enable_sm)
        self.users = UserStore(
            crypto,
            path=os.path.join(cfg.server.data_dir, "zhishu_users.db"),
        )
        self.users.bootstrap(cfg.security.admin_user, cfg.security.admin_password)
        self.auth = AuthService(cfg.security, users=self.users)
        # 数据脱敏器（合规）：注入审计日志落库前的 PII 遮蔽
        self.redactor = Redactor(cfg.security.enable_redact)
        set_default_redactor(self.redactor)
        self.audit = AuditLog(
            path=os.path.join(cfg.server.data_dir, "zhishu_audit.db"),
            enable=cfg.security.enable_audit,
            redactor=self.redactor,
        )
        # Provider 运行时存储（持久化并作用于 cfg.providers）
        self.providers = ProviderStore(
            cfg,
            path=os.path.join(cfg.server.data_dir, "providers.json"),
            crypto=crypto,
        )
        # 运行期凭证池（多源优先级 + 临时不可用 + 刷新），供回退链消费
        self.credential_pool = CredentialPool(self.providers)
        self.kb = KnowledgeBase(cfg.embedding, cfg.vector_store, cfg.server.data_dir, app_cfg=cfg)
        self.memory = MemoryStore(path=os.path.join(cfg.server.data_dir, "zhishu_memory.db"))
        # 记忆管理器：内置会话记忆（Agent 直管） + 至多一个外部向量 provider（opt-in）
        self.memory_manager = MemoryManager(
            cfg, cfg.server.data_dir, builtin_store=self.memory
        )
        self.tool_ctx = ToolContext(kb=self.kb, security=cfg.security)
        # 多模态产物存储（图像/视频落盘，经 /media 同源托管）
        self.media = MediaStore(
            root=os.path.join(cfg.server.data_dir, cfg.media.store_dir),
            url_prefix="/media",
        )
        # 多用户对话存储（按 owner 隔离，管理员可看全部）
        self.conversations = ConversationStore(
            path=os.path.join(cfg.server.data_dir, "zhishu_conversations.db"),
        )
        # 模块运行时集成器：连接 MCP、注册插件/MCP 工具，供 Agent 调用
        self.modules = ModuleIntegrator(cfg)
        # 定时任务调度器（生命周期在 main lifespan 中 start/stop）
        self.cron = CronScheduler(cfg)

    def build_agent(self, owner: str | None = None) -> "Agent":
        """构造一个会话级 Agent 实例（对话接口与定时任务共用，保证行为一致）。"""
        return Agent(
            self.cfg, self.llm, self.kb, self.memory, self.tool_ctx,
            media=self.media,
            context_engine=build_context_engine(self.cfg, self.llm),
            memory_manager=self.memory_manager,
        )


_CTX: AppContext | None = None


def init_ctx(cfg: ZhishuConfig) -> AppContext:
    global _CTX
    _CTX = AppContext(cfg)
    return _CTX


def get_ctx() -> AppContext:
    if _CTX is None:
        raise RuntimeError("应用上下文未初始化，请先调用 init_ctx()")
    return _CTX
