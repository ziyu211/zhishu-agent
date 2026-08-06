/**
 * 智枢智能体 —— 共享 TypeScript 接口
 * 按域组织，供各 api/<domain>.ts 与 stores 复用（对标 hermes-web-ui 在每个 api 模块内联手写接口）。
 *
 * 约定：列表类接口后端统一以 {key:[...]} 包裹返回（与 hermes 一致），故此处
 * 相应声明为包裹对象（如 SkillsResp），视图/store 解包 .skills / .plugins 等。
 */

// ─── 鉴权 ───────────────────────────────────────────────
export interface AuthStatus {
  auth_enabled: boolean
  password_login: boolean
  user_count: number
}
export interface LoginResp {
  token: string
  token_type?: string
  user?: string
  role?: string
  role_label?: string
  display_name?: string
  perms?: string[]
}
export interface ChangePasswordReq {
  old_password: string
  new_password: string
}

// ─── 模型 / Provider ───────────────────────────────────
export interface ModelGroup {
  provider: string
  label?: string
  models: string[]
  local?: boolean
  base_url?: string
  has_key?: boolean
}
export interface ModelsResp {
  providers: ModelGroup[]
  default_model: string
}
export interface ProvidersResp {
  default_model: string
  providers: Provider[]
}
export interface Provider {
  name: string
  type: string
  api_key?: string
  api_key_masked?: string
  base_url?: string
  models?: string[]
  enabled?: boolean
  local?: boolean
  has_key?: boolean
  priority?: number
  builtin?: boolean
  owner?: string
  shared?: boolean
  share_with?: string[]
  context_length?: number | null   // 上下文窗口 token；null=未知
}
export interface RemoteModelsResp {
  models: string[]
}

// ─── 用户管理 ───────────────────────────────────────────
export interface UserItem {
  id: number
  user: string
  role: string
  role_label?: string
  display_name?: string
  status?: string        // active | disabled
  created_at?: string
  last_login?: string
}
export interface RoleItem {
  value: string         // 后端返回 value（非 role），前端据此取值
  label: string
  perms?: string[]
}
export interface UsersResp {
  users: UserItem[]
}
export interface RolesResp {
  roles: RoleItem[]
}

// ─── 对话 / 会话 ───────────────────────────────────────
export interface Conversation {
  id: string
  title?: string
  owner?: string
  pinned?: boolean
  updated_at?: string
  messages?: any[]
}
export interface ConversationListResp {
  conversations: Conversation[]
}
export interface ConversationCreateReq {
  title?: string
  id?: string
}

// ─── 知识库 ─────────────────────────────────────────────
export interface DocumentItem {
  doc_id: string
  title: string
  file_type?: string
  size_bytes?: number
  owner?: string
  created_at?: string
  chunk_count?: number
  char_count?: number
  content?: string
  /** 保留的原始文件路径；存在则可「重新解析」 */
  raw_path?: string
}
export interface DocumentDetail extends DocumentItem {
  content: string
}
export interface DocumentsResp {
  documents: DocumentItem[]
  scope?: string
  owner?: string
  total?: number
}
export interface KnowledgeStats {
  backend: string
  embedding_dim: number
  vectors: number
  documents: number
}
export interface KnowledgeSearchHit {
  text: string
  score: number
  doc_id?: string
  title?: string
  meta?: Record<string, any>
}
export interface KnowledgeSearchResp {
  query: string
  hits: KnowledgeSearchHit[]
}
export interface UploadFileResp {
  doc_id: string
  title: string
  file_type?: string
}
export interface ReparseResp {
  ok: boolean
  doc_id: string
  title?: string
  file_type?: string
  chunks?: number
}

// ─── 附件解析（上传到对话框后解析） ──────────────────
export interface NeedPlugin {
  name: string
  description: string
  version?: string
}
export interface AttachParseResp {
  attachment_id: string
  file_id: string
  title: string
  file_type: string
  is_image: boolean
  url: string | null
  // 仅落盘、不解析：stored=已上传待解析；后续由 read_file 工具按需解析
  stored_path: string | null
  status: 'stored' | 'parsing' | 'done' | 'error' | 'installing'
  parsed: boolean
  text: string | null
  text_total: number | null
  doc_id: string | null
  vision_available: boolean
  needs_plugin: NeedPlugin | null
  parse_error: string | null
}

// ─── 管理 / 审计 ───────────────────────────────────────
export interface AdminStatus {
  auth_enabled: boolean
  sm_enabled: boolean
  audit_enabled: boolean
  outbound_allowed: boolean
  providers: string[]
  default_model: string
  knowledge_base: { vectors?: number; documents?: number } | any
  tools: string[]
}
export interface MemorySettings {
  vector_enabled: boolean
  vector_top_k: number
}
export interface SecuritySettings {
  /** 允许从内网/私有地址拉取模型列表（/models/fetch） */
  allow_private_fetch: boolean
  /** 工具是否允许出网 */
  outbound_allow: boolean
  /** 是否允许代码执行 */
  allow_code_exec: boolean
  /** Shell 执行总闸 */
  allow_shell: boolean
  /** 是否强制 Shell 白名单 */
  shell_enforce_allowlist: boolean
  /** 审计日志 */
  enable_audit: boolean
  /** 数据脱敏 */
  enable_redact: boolean
}
export interface SettingsResp {
  memory: MemorySettings
  security: SecuritySettings
}
export interface AuditItem {
  ts: string
  user?: string
  action: string
  detail?: string
  ip?: string
}
export interface AuditResp {
  records: AuditItem[]
}

// ─── 技能 / 插件 / MCP / 工具 ──────────────────────────
export interface SkillItem {
  name: string
  description?: string
  enabled: boolean
  source?: string
  owner?: string
  shared?: boolean
  share_with?: string[]
}
export interface PluginItem {
  name: string
  description?: string
  enabled: boolean
  version?: string
  source?: string
  owner?: string
  shared?: boolean
  share_with?: string[]
}
export interface McpItem {
  name: string
  description?: string
  enabled: boolean
  status?: string
  tools?: string[]
  config?: Record<string, any>
  owner?: string
  shared?: boolean
  share_with?: string[]
}
export interface ToolItem {
  name: string
  description?: string
  source?: string
}
export interface SkillsResp {
  skills: SkillItem[]
}
export interface PluginsResp {
  plugins: PluginItem[]
}
export interface McpResp {
  servers: McpItem[]
}
export interface ToolsResp {
  tools: ToolItem[]
  count: number
}

// ─── 子智能体（多 Agent 协作成员） ─────────────────────
export interface AgentItem {
  name: string
  description?: string
  version?: string
  enabled: boolean
  system_prompt?: string
  model?: string
  max_steps?: number
  tools?: string[] | 'all' | 'none'
  tools_mode?: 'all' | 'none' | 'custom'
  created_at?: string
  tool_count?: number
  owner?: string
  shared?: boolean
  share_with?: string[]
}
export interface AgentDetail extends AgentItem {}
export interface AgentsResp {
  agents: AgentItem[]
}
export interface AgentOptionsResp {
  agents: { name: string; description?: string }[]
}

// ─── 记忆 ───────────────────────────────────────────────
export interface MemoryData {
  memory?: string
  user?: string
  soul?: string
}
export interface MemoryExport extends MemoryData {
  combined?: string   // 三文件合并导出内容
  raw?: string
}

// ─── 定时任务 ───────────────────────────────────────────
// 注：CronJob / CronRun 的权威类型定义见 @/api/cron.ts（与运行时后端载荷一致），
// 此处不再重复声明，避免与后端契约出现不一致。

// ─── 流式对话事件（SSE） ───────────────────────────────
export interface ChatEvent {
  type:
    | 'token'
    | 'status'
    | 'image'
    | 'video'
    | 'error'
    | 'tool_call'
    | 'tool_result'
    | 'delegate_start'
    | 'delegate_end'
    | 'run_failed'            // 工具/解析失败：前端静默吞错，不阻断主流程
    | 'done'
  text?: string
  url?: string
  message?: string
  name?: string
  args?: any
  result?: any
  task?: string
  agent?: string
}
