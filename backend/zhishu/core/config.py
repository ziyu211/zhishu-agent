"""智枢智能体 —— 配置中心。

配置来源（优先级从低到高）：
  1. 内置默认值（含国产 Provider 端点）
  2. YAML 文件（--config 指定）
  3. 环境变量（ZHISHU_*）
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

import yaml

from .shellguard import DEFAULT_SHELL_ALLOWLIST


# ---------------------------------------------------------------------------
# 国产 LLM Provider 预设（OpenAI 兼容协议）
# base_url 均为国内可达端点；本地 Ollama 走内网。
# ---------------------------------------------------------------------------
DEFAULT_PROVIDERS: dict[str, dict] = {
    "qwen": {  # 通义千问（阿里云 DashScope，国内）
        "label": "通义千问 Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen2.5-72b-instruct"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "deepseek": {  # DeepSeek（国内）
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "zhipu": {  # 智谱 GLM（国内）
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paite/v1",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "kimi": {  # Kimi（月之暗面，国内）
        "label": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "ernie": {  # 文心一言（百度，国内）
        "label": "文心一言 ERNIE",
        "base_url": "https://qianfan.baidubce.com/v2",
        "models": ["ernie-4.0-8k", "ernie-3.5-8k", "ernie-speed-8k"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "minimax": {  # MiniMax（国内）
        "label": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "models": ["abab6.5-chat", "abab5.5-chat"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "spark": {  # 讯飞星火（国内）
        "label": "讯飞星火 Spark",
        "base_url": "https://spark-api-open.xf-yun.com/v1",
        "models": ["generalv3.5", "generalv4"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "ollama": {  # 本地推理（Ollama，内网离线）。默认不启用：需在配置/界面显式开启
        "label": "本地 Ollama（离线）",
        "base_url": "http://127.0.0.1:11434/v1",
        "models": ["qwen2.5:7b", "qwen2.5:14b", "glm4:9b"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
        "local": True,
        "enabled": False,
    },
    "vllm": {  # 本地 / 国产推理（vLLM / 昇腾 / Paddle，内网）。默认不启用
        "label": "本地 vLLM（离线）",
        "base_url": "http://127.0.0.1:8000/v1",
        "models": ["local-model"],
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
        "local": True,
        "enabled": False,
    },
}


# ---------------------------------------------------------------------------
# 模型「模态类型」推断
# 智枢引擎按类型分流：text → /chat/completions；image → /images/generations；
# video → /videos（异步）。仅凭模型名做鲁棒的关键字匹配，离线可用、无需额外配置。
# ---------------------------------------------------------------------------
_VIDEO_HINTS = (
    "video", "sora", "kling", "cogvideo", "hunyuanvideo", "wan-video", "wanx-video",
    "seedance", "vidu", "hailuo", "veo", "runway", "生视频", "视频",
)
_IMAGE_HINTS = (
    "image", "dall-e", "dalle", "gpt-image", "stable-diffusion", "sd3", "sdxl",
    "flux", "cogview", "wanx", "wanx2", "kolors", "seedream", "hunyuan-image",
    "irag", "画", "绘",
)


def classify_model(name: Optional[str]) -> str:
    """根据模型名推断模态类型：返回 'text' | 'image' | 'video'。

    规则：先判视频（含 video 等关键字），再判图像（含 image/绘图系关键字），
    其余归为文本对话。匹配大小写不敏感。
    """
    n = (name or "").lower()
    if not n:
        return "text"
    if any(h in n for h in _VIDEO_HINTS):
        return "video"
    if any(h in n for h in _IMAGE_HINTS):
        return "image"
    return "text"


@dataclass
class ProviderConfig:
    name: str
    label: str
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    local: bool = False
    enabled: bool = True
    priority: int = 100  # 越小越优先（回退链顺序）
    mode: str = ""        # 扩展模式：空=普通 LLM；"moa"=多智能体 facade（并行聚合）
    # 推理框架兼容画像（见 core/providers/compat.py）：
    #   ""=自动探测（按 base_url / 端口推断）；openai / vllm / sglang / lmdeploy /
    #   mindie / ollama / xinference / tgi / llamacpp / generic
    # 各框架对「OpenAI 兼容」的实现差异很大（system 位置、content:null、tools 支持、
    # 未知字段容忍度），这里显式声明后可在请求前规避，避免先吃一次 4xx 再自愈。
    compat: str = ""
    # 上下文窗口（token）。用户在「模型管理」中填写，为空表示未知（按全局默认预算处理）。
    # 生效点：ContextEngine 按此预算裁剪/压缩历史，避免请求超出模型窗口被服务端 400 拒绝。
    context_length: Optional[int] = None
    # ---- 多用户隔离 ----
    owner: str = ""       # 归属用户；空=历史系统级（全员可见，仅 admin 可管理）
    shared: bool = False  # 显式共享：对他人可见可用（共享后他人可用其密钥，但密钥对其脱敏）
    share_with: list[str] = field(default_factory=list)  # 角色级共享：仅这些角色可见可用（shared=False 时生效）


@dataclass
class EmbeddingConfig:
    backend: str = "provider"   # provider(走配置的模型Provider网络embeddings) | local(transformers) | ollama | hash
    provider: Optional[str] = None   # 指定用于 embedding 的 Provider 名；None 则跟随 default_model 解析
    embed_model: str = ""       # provider 后端用于 /embeddings 的「模型名」（与聊天模型区分）；
                                # 空则回退 pc.models[0]（多半是聊天模型，很可能不支持 embeddings）
    fallback_hash: bool = True  # provider 网络 embedding 调用失败时是否优雅降级为 hash（避免中断上下文组装）
    model: str = "bge-small-zh"   # transformers 模型名（backend=local 时生效）
    ollama_model: str = "bge-m3:latest"
    ollama_base: str = "http://127.0.0.1:11434"
    dim: int = 512          # hash 降级维度
    # 降级隔离（见 core/vector_store.py 的 emb_sig）：hash 伪向量与真实语义向量
    # 不在同一空间，混存会让检索结果失真（维度不同还会直接抛异常）。
    #   strict_ingest=True  —— 入库时若发生降级则**拒绝写入**并报错，宁可失败也不脏库；
    #   strict_ingest=False —— 允许写入，但会打上降级签名，检索时自动隔离（该文档
    #                          在模型恢复后检索不到，需重新解析）。
    strict_ingest: bool = True


@dataclass
class VectorStoreConfig:
    backend: str = "sqlite"  # sqlite | milvus | pgvector | dm
    path: str = "data/zhishu_vector.db"
    # milvus / pg / dm 连接（按需）
    host: str = "127.0.0.1"
    port: int = 19530
    username: str = ""
    password: str = ""
    database: str = "zhishu"


@dataclass
class MediaConfig:
    """图像 / 视频生成参数与产物存储。"""
    # 图像
    image_size: str = "1024x1024"
    image_path: str = "/images/generations"   # 相对 provider.base_url 的端点
    # 视频（异步：提交 + 轮询）
    video_size: str = "1152x768"
    video_num_frames: int = 121               # 需满足 8n+1（81/121/241...）
    video_frame_rate: int = 24                # 1~60
    video_path: str = "/videos"               # 提交端点
    video_poll_path: str = "/videos/{task_id}"  # 轮询端点
    video_poll_interval: float = 5.0          # 轮询间隔（秒）
    video_timeout: float = 600.0              # 单次生成最长等待（秒）
    # 产物落盘目录（相对 data_dir），经 /media 同源托管，避免临时 URL 过期
    store_dir: str = "generated"
    # 上游瞬时失败重试（应对 Agnes 等服务的 429/5xx Service busy）
    max_retries: int = 4                      # 最多重试次数（不含首次）
    retry_base_delay: float = 2.0             # 指数退避基期（秒）
    retry_max_delay: float = 30.0             # 单次退避上限（秒）
    retry_codes: tuple = (408, 425, 429, 500, 502, 503, 504)  # 视为可重试的状态码


@dataclass
class AgentConfig:
    """Agent 运行时行为（对标 Hermes agent 运行期可配置项，均为 opt-in）。"""
    compression_enabled: bool = False       # 上下文压缩（长对话自动摘要）
    compression_threshold: int = 24         # 历史轮数超过此值才压缩
    compression_keep_recent: int = 8        # 压缩时保留最近 N 轮原样
    compression_tool_result_max: int = 4000 # 单条工具结果超过此字符数则摘要（0=关闭）
    moa_enabled: bool = False               # 多智能体(MoA) facade 总开关
    moa_provider: str = ""                  # 配置为 mode="moa" 的 provider 名
    moa_reference_models: list[str] = field(default_factory=list)  # 参考模型列表
    moa_aggregator: str = ""                # 聚合模型（空=默认模型）
    skills_progressive: bool = False       # 技能渐进披露（仅列清单+read_skill，默认全量注入）
    # 技能自进化闭环（对标 Hermes learning loop）：复杂任务完成后由 LLM 蒸馏沉淀为技能文件。
    # 安全护栏：仅当步数/工具调用数达标、且任务成功（到达 done）时触发；仅写入技能目录并审计。
    skills_auto_learn: bool = False
    skills_auto_learn_min_steps: int = 8    # 至少消耗的推理步数（含工具轮）
    skills_auto_learn_min_tools: int = 3    # 至少调用工具次数
    # 图片输入模式（决定附件图片如何进入对话；系统始终不内置 OCR）：
    #   auto   : 默认。把图片作为视觉参考以 base64 vision content part 传给模型
    #            （即 Hermes 的 native 模式；若 provider 实际不支持视觉会报错，可改 text）
    #   native : 同 auto，显式声明走视觉部件
    #   text   : 关闭视觉，仅提示「模型无视觉能力」，图片内容不进入模型
    image_input_mode: str = "auto"
    # 会话内 nudge（对标 Hermes _memory_nudge_interval / _skill_nudge_interval）：
    # 每完成 N 次工具调用，向模型注入一条内部提醒，提示它把可复用的工作流沉淀为
    # 长期记忆(memory 工具)或技能(技能自学习)。0=关闭。
    nudge_interval: int = 8
    # 后台记忆反思（对标 Hermes background_review）：每轮成功回答后，fork 一次廉价 LLM
    # 调用，从本轮对话中蒸馏出值得长期记住的用户事实/偏好，追加进 MEMORY.md / USER.md。
    # 默认关闭（避免意外 token 消耗与记忆污染），由运维按需开启。
    reflection_enabled: bool = False
    # 单次生成的最大输出 token 数（LLMClient.chat/stream 的 max_tokens 上限）。
    # 此前默认 2048，导致「全面分析/长文总结」等复杂任务的最终回答被静默截断
    # （表现为「回复内容不完整」）。上调到 8192 以容纳较长的结构化分析/报告；
    # 现代模型（如远程 sensenova-6.7-flash-lite）实测可稳定接受 16384 而不报错。
    # 若个别 Provider/模型对输出长度有更低硬上限，可在此下调。
    max_tokens: int = 8192
    # ---- 企业级多用户保护（并发 / 配额 / 委派熔断）----
    # 全局并发对话上限（顶层 chat 请求，不含嵌套委派）；0=不限制。
    # 防止单实例被少量长任务（多智能体/大文档）占满事件循环，拖垮全员。
    max_concurrent_global: int = 0
    # 单用户并发对话上限；0=不限制。避免单个用户开 N 个长会话耗尽实例资源。
    max_concurrent_per_user: int = 0
    # 单用户每日对话额度（按自然日计数，落盘持久化）；0=不限制。
    # 企业按席位/用量计费的轻量落地，重启后从 quota_usage.json 恢复，跨日自动清零。
    daily_quota_per_user: int = 0
    # 委派（子智能体）整体超时（秒）；0=不限制。
    # 防止子智能体因工具卡死/模型长思考拖挂主 SSE 流（曾表现为「长任务不出结果」）。
    # 超时后中止该次委派并返回错误串给主管，不污染主会话。
    delegate_timeout: float = 300.0
    # 委派最大深度：主管(depth=0) 可委派协调类子智能体(depth=1)，协调类再分派给
    # 执行型子智能体(depth=2)。设为 1 时协调类子智能体不能再委派（只支持 主管→单级子智能体）；
    # 设为 3 支持「主管→总监(Orchestrator)→研究员/因子/策略/风控」两级编排，同时以
    # depth>=3 为递归地板（任何子智能体最多再委派一层），从根上杜绝 A→B→A 递归风暴。
    delegate_max_depth: int = 3
    # ---- 反空转熔断 + 工具并发（Task #395，均为 opt-in，默认开启）----
    # 同一 LLM 响应返回多个「非委派」叶子工具时并发执行（I/O 密集，安全），
    # 缩短多工具步的端到端耗时（对标 Hermes 分段并行）。关闭则回退串行。
    parallel_tools: bool = True
    parallel_tool_workers: int = 5            # 叶子工具并发上限（信号量）
    # 连续失败熔断：连续 N 次工具调用均失败/被拦截（如 shell 白名单拦截 apt-get、
    # 依赖缺失、权限不足）即终止，避免「失败→重试→再失败」反复耗尽 16 步。
    tool_fail_break: int = 6
    # 重复失败循环熔断：同一 (工具名, 归一化参数) 反复调用且均失败累计 N 次即终止，
    # 兜底捕获「code_exec 装 Node → terminal_run 装 Node 被拦 → 再 code_exec…」式死循环
    # （该循环成功/失败交替出现，连续失败计数会被成功调用清零，故需独立按签名累计）。
    tool_cycle_break: int = 4
    # 工具步骤硬上限（独立于 16 步循环上限）：即便异常步/工具组合也保证终止，防止失控。
    max_tool_steps: int = 64
    # ---- Prompt 缓存（对标 Hermes prompt_caching.py，Task #398，opt-in，默认 auto）----
    # 把「稳定前缀（身份/指令/工具定义）」与「易变内容（检索结果、当前轮输入）」用
    # cache_control 断点隔开，使 Provider 的 KV 前缀缓存命中，缩短多步推理的重复前缀耗时。
    #   off   : 完全不注入（等价于旧行为，零风险）
    #   auto  : 按 Provider 家族自动选策略——Anthropic/DeepSeek 注入 cache_control 断点、
    #           Qwen 走 extra_body.prompt_cache、OpenAI/Azure/未知走服务端自动前缀缓存、
    #           本地 Ollama/vLLM 跳过（不注入标记，避免 400）
    #   force : 对所有 Provider 按 Anthropic 风格注入 cache_control（专家模式，适用于明确
    #           支持该标记的网关；不熟悉的 Provider 可能 400，慎用）
    prompt_cache: str = "auto"


@dataclass
class MemoryConfig:
    """长期记忆后端（对标 Hermes MemoryProvider）。"""
    vector_enabled: bool = False            # 向量长期记忆（跨会话语义召回）
    vector_top_k: int = 5                   # 召回条数
    backend: str = "builtin"                # 可插拔记忆后端：builtin | mem0（默认 builtin，零回归）


@dataclass
class SecurityConfig:
    enable_auth: bool = True
    admin_user: str = "admin"
    admin_password: str = "zhishu@2026"   # 生产务必修改
    secret: str = "change-me-zhishu-secret"  # 签名密钥
    enable_sm: bool = True                 # 国密（缺失库则降级）
    enable_audit: bool = True
    enable_redact: bool = True             # 数据脱敏（PII 正则遮蔽，落库/输出前生效）
    outbound_allow: bool = False           # 工具是否允许出网（默认否）
    allow_private_fetch: bool = False      # /models/fetch 是否允许拉取内网/私有地址的模型列表（默认否，防 SSRF）
    # 自扩展代码执行（对标 Hermes 自创工具能力）：在内网可信部署下允许智能体
    # 生成 Python 并（一次性或注册为可复用工具）执行，以处理标准工具不支持的文件/任务。
    # 护栏：子进程沙箱 cwd、超时、内存上限、禁网络、输出截断。生产可按需关闭。
    allow_code_exec: bool = True
    code_exec_timeout: int = 30            # 子进程默认超时（秒），上限 120
    code_exec_mem_limit_mb: int = 0        # 子进程内存上限(MB)，0=不限制
    # --- Shell 闸门（cron shell 任务 + terminal_run 工具共用，见 core/shellguard.py）---
    # 此前 cron 的 shell 动作在宿主机裸跑：不过滤命令、继承全量环境变量（含密钥）、
    # 超时只杀直接子进程。下列开关为纵深防御，默认开启白名单。
    allow_shell: bool = True               # 总闸：关闭后 cron shell / terminal_run 一律拒绝
    shell_enforce_allowlist: bool = True   # 是否强制可执行文件白名单（关闭仅保留高危拒绝清单）
    shell_allowlist: list = field(default_factory=lambda: list(DEFAULT_SHELL_ALLOWLIST))
    shell_timeout: int = 300               # cron shell 单次执行上限（秒）
    shell_mem_limit_mb: int = 1024         # 子进程内存上限(MB)，0=不限制（仅 POSIX 生效）


@dataclass
class WebSearchConfig:
    """网页搜索（对标 Hermes web_search，配置驱动多后端）。

    受 security.outbound_allow 出网闸门约束：闸门关闭时工具直接拦截。
    backend:
      * bing_cn    —— 零 Key，解析中国必应 HTML 结果页（默认，国内可达）
      * duckduckgo —— 零 Key，解析 DuckDuckGo HTML 结果页（海外网络）
      * tavily     —— 需 api_key（https://tavily.com）
      * bing       —— 需 api_key（Azure Bing Web Search v7）
    零 Key 后端失败时自动互备（bing_cn <-> duckduckgo）。
    """
    backend: str = "bing_cn"
    api_key: str = ""
    max_results: int = 5
    timeout: int = 15


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 1
    static_dir: str = ""      # 前端构建产物目录，空则跳过静态托管
    data_dir: str = "data"


@dataclass
class CronConfig:
    """定时任务（对标 Hermes cron 调度器，内网合规版）。"""
    enabled: bool = True              # 是否启动调度器
    store_dir: str = "cron"           # 任务/历史持久化目录（相对 data_dir）
    max_concurrency: int = 2          # 同时运行任务上限
    default_model: str = ""           # 留空则用全局默认模型


@dataclass
class ZhishuConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = field(default_factory=VectorStoreConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    default_model: str = ""  # 留空 = 未配置；运行时按已配置可用的 Provider 自动解析（不再内置预设模型）
    defaults: dict[str, str] = field(default_factory=dict)  # 每用户默认模型：{username: "provider/model"}
    system_prompt: str = (
        "你是智枢智能体，一个部署在用户内网的国产自主可控 AI 助手。"
        "你应当严谨、可靠，优先使用用户提供的知识库与工具完成任务。"
    )

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ZhishuConfig":
        data: dict = {}
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        # 1) providers：从预设填充，再用 YAML 覆盖
        providers: dict[str, ProviderConfig] = {}
        for name, preset in DEFAULT_PROVIDERS.items():
            pc = ProviderConfig(name=name, **preset)
            providers[name] = pc
        for name, ov in (data.get("providers") or {}).items():
            base = providers.get(name, ProviderConfig(name=name, label=name, base_url=""))
            for k, v in ov.items():
                if k in ("name",):
                    continue
                setattr(base, k, v)
            providers[name] = base

        # 环境变量注入 Key（ZHISHU_<PROVIDER>_KEY）
        for name, pc in providers.items():
            env_key = os.environ.get(f"ZHISHU_{name.upper()}_KEY")
            if env_key:
                pc.api_key = env_key

        cfg = cls(
            server=_from_dict(ServerConfig, data.get("server")),
            security=_from_dict(SecurityConfig, data.get("security")),
            embedding=_from_dict(EmbeddingConfig, data.get("embedding")),
            vector_store=_from_dict(VectorStoreConfig, data.get("vector_store")),
            media=_from_dict(MediaConfig, data.get("media")),
            agent=_from_dict(AgentConfig, data.get("agent")),
            memory=_from_dict(MemoryConfig, data.get("memory")),
            cron=_from_dict(CronConfig, data.get("cron")),
            web_search=_from_dict(WebSearchConfig, data.get("web_search")),
            providers=providers,
            default_model=os.environ.get("ZHISHU_DEFAULT_MODEL", data.get("default_model", "") or ""),
            system_prompt=data.get("system_prompt", cls().system_prompt),
        )
        # 仅保留 enabled 的 provider
        cfg.providers = {k: v for k, v in cfg.providers.items() if v.enabled}
        # 环境变量覆盖：出网闸门。容器内 /app/deploy/zhishu.yaml 写在可写层，
        # 一旦 docker rm 重建容器即回到镜像默认 False；用 -e ZHISHU_OUTBOUND_ALLOW=1
        # 可在部署时固化，避免依赖镜像可写层（仅在显式设置时覆盖 YAML 值）。
        _env_outbound = os.environ.get("ZHISHU_OUTBOUND_ALLOW")
        if _env_outbound is not None:
            cfg.security.outbound_allow = _env_outbound.strip().lower() in ("1", "true", "yes", "on")
        return cfg

    def get_provider(self, key: str) -> Optional[ProviderConfig]:
        """key 形如 'ollama' 或 'ollama/qwen2.5:7b'。"""
        if "/" in key:
            name, _model = key.split("/", 1)
        else:
            name, _model = key, None
        return self.providers.get(name)

    def usable_providers(self) -> list["ProviderConfig"]:
        """返回「真正可用」的 Provider：enabled 且（已配 API Key 或为本地端点）。按 priority 升序。"""
        return sorted(
            [p for p in self.providers.values() if p.enabled and (p.api_key or p.local)],
            key=lambda x: x.priority,
        )

    def resolve_model(self, key: Optional[str]) -> tuple[ProviderConfig, str]:
        """解析为 (provider, model)。完全跟随配置，无内置预设模型：

        * key/default_model 已配置 → 严格按其指定的 Provider 解析（找不到即报错，不悄悄回退）。
        * 均未配置 → 从「已配置可用」的 Provider（enabled 且有 Key 或本地）按优先级取第一个。
        * 什么都没配置 → 抛出明确错误，提示去「模型管理」配置。
        """
        key = key or self.default_model
        if not key:
            for p in self.usable_providers():
                model = p.models[0] if p.models else "local-model"
                return p, model
            raise RuntimeError(
                "未配置默认模型，且不存在可用的 LLM Provider。"
                "请在「模型管理」中配置 Provider（API Key 或本地端点）并设置默认模型。"
            )
        if "/" in key:
            name, model = key.split("/", 1)
        else:
            name, model = key, None
        pc = self.providers.get(name)
        if not pc or not pc.enabled:
            raise RuntimeError(
                f"模型「{key}」对应的 Provider「{name}」未配置或未启用，"
                "请在「模型管理」中检查配置。"
            )
        if not model:
            model = pc.models[0] if pc.models else "local-model"
        return pc, model

    def context_length_of(self, key: Optional[str]) -> Optional[int]:
        """解析某模型（"provider/model" 或默认模型）配置的上下文窗口 token 数。

        未配置 / 解析失败 → None（调用方按「未知」处理，不做窗口裁剪）。
        本方法是用户在「模型管理」填写的 context_length 的**唯一生效入口**，
        供 ContextEngine 计算历史预算，避免请求超出窗口被服务端 400 拒绝。
        """
        try:
            pc, _ = self.resolve_model(key)
        except Exception:
            return None
        n = getattr(pc, "context_length", None)
        try:
            n = int(n) if n is not None else None
        except (TypeError, ValueError):
            return None
        return n if (n and n > 0) else None

    def ordered_providers(self, include_disabled: bool = False) -> list[ProviderConfig]:
        vals = self.providers.values()
        if not include_disabled:
            vals = [p for p in vals if p.enabled]
        return sorted(vals, key=lambda x: x.priority)

    def for_user(self, username: Optional[str], is_admin: bool = False,
                  user_role: Optional[str] = None) -> "ZhishuConfig":
        """返回仅含该用户可见 Provider（owner 为空=公共、owner==username、shared、或 角色命中）及该用户默认模型的配置副本。

        用于「按用户隔离模型」：每个用户只能用自己的/共享的/角色共享的/公共 Provider 与默认模型。
        admin（is_admin=True）返回自身（可见全部 Provider）；匿名/未登录仅见共享项（无角色则无角色共享）。
        """
        if is_admin:
            return self
        if not username:
            # 匿名：仅共享 Provider，且不泄露任何私有密钥对应的默认模型
            vis = {n: p for n, p in self.providers.items() if p.shared}
            new = copy.copy(self)
            new.providers = vis
            new.default_model = ""
            return new
        vis = {
            n: p for n, p in self.providers.items()
            if (not p.owner)                       # 历史系统级/公共 Provider（owner 为空）：全员可见，仅 admin 可改
            or p.owner == username or p.shared
            or (p.share_with and user_role in p.share_with)
        }
        new = copy.copy(self)
        new.providers = vis
        new.default_model = self.defaults.get(username, self.default_model)
        return new

    @staticmethod
    def presets() -> list[dict]:
        """内置国产/本地 Provider 预设，供前端"添加 Provider"表单使用。"""
        out = []
        for name, p in DEFAULT_PROVIDERS.items():
            out.append({
                "provider": name,
                "label": p["label"],
                "base_url": p["base_url"],
                "models": p.get("models", []),
                "local": p.get("local", False),
            })
        return out


def _from_dict(cls, d: Optional[dict]):
    d = d or {}
    valid = {f for f in cls.__dataclass_fields__}
    return cls(**{k: v for k, v in d.items() if k in valid})
