/**
 * 智枢智能体 —— 共享 TypeScript 接口
 * 按域组织，供各 api/<domain>.ts 与 stores 复用（对标 hermes-web-ui 在每个 api 模块内联手写接口）。
 */

// ─── 鉴权 ───────────────────────────────────────────────
export interface AuthStatus {
  authenticated: boolean
  user?: string
  role?: string
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
}
export interface ModelsResp {
  providers: ModelGroup[]
  default_model: string
}
export interface Provider {
  name: string
  type: string
  api_key?: string
  base_url?: string
  models?: string[]
  enabled?: boolean
  local?: boolean
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
  created_at?: string
}
export interface RoleItem {
  role: string
  label: string
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
export interface KnowledgeStats {
  document_count: number
  chunk_count: number
}
export interface KnowledgeSearchResp {
  results: { doc_id: string; title: string; score: number; snippet: string }[]
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
  healthy: boolean
  version?: string
  uptime?: number
}
export interface AuditItem {
  id?: number
  time?: string
  action: string
  actor?: string
  detail?: string
}

// ─── 技能 / 插件 / MCP / 工具 ──────────────────────────
export interface SkillItem {
  name: string
  description?: string
  enabled: boolean
  source?: string
}
export interface PluginItem {
  name: string
  description?: string
  enabled: boolean
  version?: string
  source?: string
}
export interface McpItem {
  name: string
  description?: string
  enabled: boolean
  status?: string
  tools?: string[]
  config?: Record<string, any>
}
export interface ToolItem {
  name: string
  description?: string
  source?: string
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
}
export interface AgentDetail extends AgentItem {}
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
  raw?: string
}

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
