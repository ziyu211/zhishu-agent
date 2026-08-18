"""智枢智能体 —— 应用上下文（单例依赖注入）。

由 main.py 启动时构建一次，供各 API 路由共享：
LLM 客户端、知识库、记忆、鉴权、审计、工具上下文。
"""
from __future__ import annotations

import json
import os

from .core.config import ZhishuConfig
from .core.llm import LLMClient
from .core.rag import KnowledgeBase
from .core.memory import MemoryStore, MemoryManager
from .core.security import AuthService, AuditLog, UserStore, Crypto
from .core.concurrency import init_limiter
from .core.redact import Redactor, set_default as set_default_redactor
from .core.credentials import ProviderStore
from .core.conversations import ConversationStore
from .core.tools import ToolContext
from .core.media import MediaStore
from .core.modules import ModuleIntegrator
from .core.agent import Agent, build_context_engine
from .core.cron import CronScheduler


# 运行时覆盖文件的 schema 版本。
# 历史上存在「override 覆盖陷阱」：老版本（或手写）override.json 把 memory.vector_enabled
# 持久化为 false，且在 YAML 显式开启记忆基线后，启动期 _apply_override 仍把它静默降级为 false，
# 导致 v1.0.35 远程部署的记忆能力实际未生效。自 v1.0.36 起，override 文件带 "_v" 版本戳；
# 任何 _v 不匹配当前版本的覆盖文件一律视为「陈旧」——丢弃其 memory/security 块，cfg 维持
# YAML 基线，并就地重写为当前版本（含 YAML 真实值），使后续启动能正常识别、管理员经 UI 的
# 显式切换也能持久化。这从根上避免陈旧 override 复活。
OVERRIDE_VERSION = 2


class AppContext:
    def __init__(self, cfg: ZhishuConfig):
        self.cfg = cfg
        self.llm = LLMClient(cfg)
        os.makedirs(cfg.server.data_dir, exist_ok=True)
        # 运行时设置覆盖（用户自助开关，如长期记忆；持久化于 data_dir/config.override.json）
        self._override_path = os.path.join(cfg.server.data_dir, "config.override.json")
        self._apply_override()
        # 多用户存储（首个 admin 由配置引导）
        crypto = Crypto(cfg.security.enable_sm)
        self.users = UserStore(
            crypto,
            path=os.path.join(cfg.server.data_dir, "zhishu_users.db"),
        )
        self.users.bootstrap(cfg.security.admin_user, cfg.security.admin_password)
        self.auth = AuthService(
            cfg.security, users=self.users,
            revoked_path=os.path.join(cfg.server.data_dir, "revoked_tokens.json"),
        )
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
        # 企业级并发/配额限流（全局信号量 + 单用户信号量 + 每日配额落盘）
        init_limiter(cfg, quota_path=os.path.join(cfg.server.data_dir, "quota_usage.json"))

    # ------------------------------------------------------------------
    # 运行时设置（用户自助开关，持久化于 config.override.json，重启后自动复现）
    # ------------------------------------------------------------------
    # 可经前台自助切换、且每次调用实时读 ctx.cfg.security.* 的运行时安全开关。
    # 这些字段不重建 AuthService/Crypto（secret/enable_auth/enable_sm 不在此列），
    # 因此可在不重启进程的情况下即时生效。
    _SECURITY_OVERRIDE_FIELDS: dict[str, type] = {
        "allow_private_fetch": bool,
        "outbound_allow": bool,
        "allow_code_exec": bool,
        "code_exec_network_isolated": bool,
        "allow_shell": bool,
        "shell_enforce_allowlist": bool,
        "enable_audit": bool,
        "enable_redact": bool,
    }

    def _apply_override(self) -> None:
        """启动时把 data_dir/config.override.json 中的设置并入 cfg。

        注意：本方法在 __init__ 早期调用，此时 self.audit / self.redactor 尚未构建，
        因此仅把数值并入 cfg（二者随后会从 cfg 初始化）；即时生效逻辑仅用于
        apply_settings 运行期路径。

        防「override 覆盖陷阱」：override 文件带 "_v" 版本戳；_v 与当前版本不符的
        陈旧文件一律丢弃其 memory/security 块（cfg 维持 YAML 基线），并就地重写为当前
        版本（含 YAML 真实值），使下次启动能正常识别、管理员经 UI 的显式切换也能持久化。
        """
        try:
            if not os.path.exists(self._override_path):
                return
            with open(self._override_path, "r", encoding="utf-8") as f:
                ov = json.load(f) or {}
        except Exception:
            return
        if ov.get("_v") != OVERRIDE_VERSION:
            # 陈旧 override：不套用其 memory/security 块（避免把 YAML 已显式开启的记忆
            # 静默降级为 false），并以 YAML 当前 cfg 值重写一份带版本戳的 override。
            self._rewrite_override_from_cfg()
            return
        self._apply_memory_cfg(ov.get("memory") or {})
        self._apply_security_cfg(ov.get("security") or {})

    def _rewrite_override_from_cfg(self) -> None:
        """以当前 cfg（YAML 基线）为准，重写 config.override.json 并打上版本戳。

        用于在检测到陈旧 override 时把 cfg 真实值固化下来，避免反复被陈旧文件覆盖，
        同时让后续启动的 _apply_override 能正常识别（_v 匹配）。
        """
        try:
            ov = {
                "_v": OVERRIDE_VERSION,
                "memory": {
                    "vector_enabled": self.cfg.memory.vector_enabled,
                    "vector_top_k": self.cfg.memory.vector_top_k,
                    "query_rewrite_enabled": self.cfg.memory.query_rewrite_enabled,
                    "extraction_enabled": self.cfg.memory.extraction_enabled,
                    "extraction_interval": self.cfg.memory.extraction_interval,
                    "extraction_model": self.cfg.memory.extraction_model,
                },
                "security": {
                    k: getattr(self.cfg.security, k)
                    for k in self._SECURITY_OVERRIDE_FIELDS
                },
            }
            os.makedirs(os.path.dirname(self._override_path), exist_ok=True)
            with open(self._override_path, "w", encoding="utf-8") as f:
                json.dump(ov, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _apply_memory_cfg(self, mem: dict) -> None:
        if isinstance(mem.get("vector_enabled"), bool):
            self.cfg.memory.vector_enabled = mem["vector_enabled"]
        if isinstance(mem.get("vector_top_k"), int) and mem["vector_top_k"] > 0:
            self.cfg.memory.vector_top_k = mem["vector_top_k"]
        if isinstance(mem.get("query_rewrite_enabled"), bool):
            self.cfg.memory.query_rewrite_enabled = mem["query_rewrite_enabled"]
        if isinstance(mem.get("extraction_enabled"), bool):
            self.cfg.memory.extraction_enabled = mem["extraction_enabled"]
        if isinstance(mem.get("extraction_interval"), int) and mem["extraction_interval"] > 0:
            self.cfg.memory.extraction_interval = mem["extraction_interval"]
        if mem.get("extraction_model") is None or isinstance(mem.get("extraction_model"), str):
            self.cfg.memory.extraction_model = mem.get("extraction_model") or None

    def _apply_security_cfg(self, sec: dict) -> None:
        for k, typ in self._SECURITY_OVERRIDE_FIELDS.items():
            v = sec.get(k)
            if isinstance(v, typ):
                setattr(self.cfg.security, k, v)

    def _apply_security_live(self) -> None:
        """让启动后已构建的审计/脱敏器跟随 cfg 当前值（仅运行期 apply_settings 可用）。"""
        if getattr(self, "audit", None) is not None:
            self.audit.enable = self.cfg.security.enable_audit
        if getattr(self, "redactor", None) is not None:
            self.redactor.enabled = self.cfg.security.enable_redact
            set_default_redactor(self.redactor)

    async def apply_settings(self, patch: dict) -> dict:
        """应用设置补丁、持久化覆盖、并使开关立即生效。

        支持 "memory"（长期记忆）与 "security"（运行时安全开关）两组；
        仅持久化本请求涉及的组，避免互相覆盖。security 组的 enable_audit /
        enable_redact 会同步到已构建的审计/脱敏器，做到免重启生效。
        """
        warnings: list = []
        if "memory" in patch:
            self._apply_memory_cfg(patch["memory"] or {})
        if "security" in patch:
            self._apply_security_cfg(patch["security"] or {})
            self._apply_security_live()
        # 重建记忆管理器（仅 memory 变更时；向量开关立即生效，无 embedding 后端时优雅降级为 None）
        # 必须在持久化之前完成：若初始化失败则回滚 vector_enabled，使下方持久化的 override
        # 与内存真实状态一致，避免在「假成功」后又被重启重新套用失效配置（闭环修复 Task #399）。
        if "memory" in patch:
            try:
                new_mm = MemoryManager(
                    self.cfg, self.cfg.server.data_dir, builtin_store=self.memory
                )
                await new_mm.initialize()
                self.memory_manager = new_mm
            except Exception as e:
                # 重建失败不得静默吞掉——否则接口返回「已开启」而实际向量记忆后端并未接上
                # （如未配置 Embedding 模型），形成「假成功」开环。回滚并附 warnings 提示根因。
                self.cfg.memory.vector_enabled = False
                warnings.append(
                    f"向量记忆后端初始化失败（长期记忆未能开启）：{e}。"
                    f"请先在 Provider 中配置可用的 Embedding 模型后重试。"
                )
        # 持久化覆盖（合并已有 override，仅更新被本请求涉及的组；重启后由 _apply_override 复现）
        try:
            ov: dict = {}
            if os.path.exists(self._override_path):
                with open(self._override_path, "r", encoding="utf-8") as f:
                    ov = json.load(f) or {}
            ov["_v"] = OVERRIDE_VERSION
            if "memory" in patch:
                ov["memory"] = {
                    "vector_enabled": self.cfg.memory.vector_enabled,
                    "vector_top_k": self.cfg.memory.vector_top_k,
                    "query_rewrite_enabled": self.cfg.memory.query_rewrite_enabled,
                    "extraction_enabled": self.cfg.memory.extraction_enabled,
                    "extraction_interval": self.cfg.memory.extraction_interval,
                    "extraction_model": self.cfg.memory.extraction_model,
                }
            if "security" in patch:
                ov["security"] = {
                    k: getattr(self.cfg.security, k)
                    for k in self._SECURITY_OVERRIDE_FIELDS
                }
            os.makedirs(os.path.dirname(self._override_path), exist_ok=True)
            with open(self._override_path, "w", encoding="utf-8") as f:
                json.dump(ov, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return {
            "memory": {
                "vector_enabled": self.cfg.memory.vector_enabled,
                "vector_top_k": self.cfg.memory.vector_top_k,
                "query_rewrite_enabled": self.cfg.memory.query_rewrite_enabled,
                "extraction_enabled": self.cfg.memory.extraction_enabled,
                "extraction_interval": self.cfg.memory.extraction_interval,
                "extraction_model": self.cfg.memory.extraction_model,
            },
            "security": {
                k: getattr(self.cfg.security, k)
                for k in self._SECURITY_OVERRIDE_FIELDS
            },
            "warnings": warnings,
        }

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
