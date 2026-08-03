import { defineStore } from 'pinia'
import { streamChat, api, getUser } from '@/api/client'
import { attachAndParse } from '@/api/chat'
import type { ChatAttachment } from '@/api/chat'
import { useAppStore } from './app'

export interface AttachmentCard {
  attachment_id?: string
  file_id?: string
  title: string
  file_type: string
  is_image: boolean
  url?: string | null
  // 仅落盘：stored=已上传待解析（由 read_file 工具按需解析）
  stored_path?: string | null
  parsed: boolean
  text?: string | null
  text_total?: number | null
  doc_id?: string | null
  vision_available?: boolean
  needs_plugin?: { name: string; description: string; version?: string } | null
  parse_error?: string | null
  status?: 'stored' | 'parsing' | 'done' | 'error' | 'installing'
}

/**
 * 多智能体委派推理链：把会话中发生的「委派 / 子智能体工具调用 / 返回」结构化，
 * 作为「主智能体的思考与推理链」喂给思维图谱的「推理步骤」Tab。
 * 字段与 useThinkingGraph 的 ThinkingStep 对齐（id/index/text/kind/depth/agent），
 * 这样面板可直接渲染，无需二次转换。
 */
export interface AgentTraceStep {
  id: string
  index: number
  text: string
  kind: 'goal' | 'observe' | 'analyze' | 'plan' | 'action' | 'conclude' | 'note'
  depth: number
  agent?: string
}

/** 多 Agent 委派关系的结构化记录：谁发出 → 调用了哪个 agent → 是否出结果。
 *  用于在「思维图谱·概念图谱」中绘制协作调用图（call graph）。 */
export interface AgentDelegation {
  id: string
  /** 委派方（调用者）。顶层主管记为 '主管(顶层)'；嵌套委派时为其父智能体。 */
  caller: string
  /** 被调用的子智能体名 */
  callee: string
  /** 委派任务摘要 */
  task: string
  /** 返回内容（可能为空） */
  result: string
  status: 'running' | 'done' | 'empty' | 'error' | 'timeout'
  startedAt: number
  endedAt?: number
}

export interface Msg {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  ts: number
  // 工具消息
  toolName?: string
  toolArgs?: string
  toolResult?: string
  toolStatus?: 'running' | 'done' | 'error'
  // 思考（部分模型以 <think> 标签输出）
  reasoning?: string
  isStreaming?: boolean
  // 多模态产物
  images?: string[]
  videos?: string[]
  statusText?: string
  errorText?: string
  // 用户上传的参考图（图生图/图生视频）—— data URI，用于消息气泡内展示
  image?: string
  // 用户上传并关联本文档的文档（写入知识库，可在气泡内点击打开）
  docs?: { doc_id: string; title: string; file_type: string; size?: number }[]
  // 上传到对话框的附件（解析结果 / 需安装插件提示），见 AttachmentCard
  attachment?: AttachmentCard
  // 多 Agent 协作：该消息由某个子智能体产生（在气泡上展示其名称标签）
  agent?: string
  // 多 Agent 协作：主智能体对本次回复的「委派推理链」（喂给思维图谱·推理步骤）
  agentTrace?: AgentTraceStep[]
  // 多 Agent 协作：本次回复的完整委派关系（喂给思维图谱·概念图谱 的协作调用图）
  agentDelegations?: AgentDelegation[]
}

/** 输入框中「待发送」的本地附件（发送前在托盘中预览，发送后剥离 file 引用）。 */
export interface PendingAttachment {
  id: string
  name: string
  type: string
  size: number
  url: string // 本地 blob URL，仅预览用；发送后由后端 /media URL 替代
  file?: File
}

// 原始文件引用（非响应式），用于「安装插件后重新解析」时再次上传
const _pendingFiles = new Map<string, File>()
// 每会话独立的请求中止控制器：支持多会话并发生成（替代原全局单 abort 锁）
const _sessionAborts = new Map<string, AbortController>()

export interface Session {
  id: string
  title: string
  owner?: string
  pinned?: boolean
  messages: Msg[]
  updatedAt: number
}

function uid(prefix = 'm'): string {
  return prefix + '_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessions: [] as Session[],
    activeId: '' as string,
    error: '' as string,
    loading: false,
    // 当前对话指定的子智能体（多 Agent 协作）：'' = 主管自动编排
    selectedAgent: '' as string,
    // 管理员在对话页是否查看全部用户会话（默认只看自己的）
    showAllSessions: false,
  }),
  getters: {
    active: (s) => s.sessions.find((x) => x.id === s.activeId) || null,
    // 「正在生成」改为仅判断当前活动会话（支持多会话并发生成），而非全局单锁：
    // 旧对话在后台跑时，新对话的输入框仍显示「发送」且可立即生成。
    streaming: (s) => {
      const cur = s.sessions.find((x) => x.id === s.activeId)
      return !!cur && cur.messages.some((m) => m.isStreaming)
    },
    sorted: (s) =>
      [...s.sessions].sort((a, b) => {
        if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1
        return b.updatedAt - a.updatedAt
      }),
  },
  actions: {
    /** 从后端加载当前用户的对话（管理员可通过 showAllSessions 看全部）。 */
    async loadSessions() {
      this.loading = true
      try {
        const app = useAppStore()
        const scope = app.isAdmin && this.showAllSessions ? 'all' : 'mine'
        const r = await api.listConversations(scope)
        this.sessions = (r.conversations || []).map((c: any) => ({
          id: c.id,
          title: c.title || '新对话',
          owner: c.owner,
          pinned: !!c.pinned,
          messages: [],
          updatedAt: c.updated_at ? new Date(c.updated_at).getTime() || Date.now() : Date.now(),
        }))
        // 载入每条对话的完整消息（懒加载，避免首屏过载）
        await Promise.all(this.sessions.map((s) => this.loadMessages(s.id)))
        if (!this.activeId && this.sessions[0]) this.activeId = this.sessions[0].id
      } catch {
        this.sessions = []
      } finally {
        this.loading = false
      }
    },

    async loadMessages(cid: string) {
      try {
        const c = await api.getConversation(cid)
        const s = this.sessions.find((x) => x.id === cid)
        if (s) s.messages = c.messages || []
      } catch {
        /* 忽略单条失败 */
      }
    },

    newSession(title = '新对话'): Session {
      const me = getUser()
      const id = uid('s')
      const s: Session = {
        id,
        title,
        owner: me?.user,
        pinned: false,
        messages: [],
        updatedAt: Date.now(),
      }
      // 后端先建（owner=当前用户），失败则仅本地存在
      api
        .createConversation({ title, id })
        .then((c: any) => {
          s.owner = c.owner || s.owner
        })
        .catch(() => {})
      this.sessions.unshift(s)
      this.activeId = s.id
      return s
    },

    ensureActive(): Session {
      if (this.active) return this.active
      return this.newSession()
    },

    switchSession(id: string) {
      this.activeId = id
    },

    /** 指定会话是否正在生成（按会话隔离，支持多会话并发）。 */
    isSessionStreaming(id: string): boolean {
      const s = this.sessions.find((x) => x.id === id)
      return !!s && s.messages.some((m) => m.isStreaming)
    },

    renameSession(id: string, title: string) {
      const s = this.sessions.find((x) => x.id === id)
      if (s) {
        s.title = title
        this.persistSession(s)
      }
    },

    togglePin(id: string) {
      const s = this.sessions.find((x) => x.id === id)
      if (s) {
        s.pinned = !s.pinned
        this.persistSession(s)
      }
    },

    removeSession(id: string) {
      this.sessions = this.sessions.filter((x) => x.id !== id)
      if (this.activeId === id) this.activeId = this.sessions[0]?.id || ''
      api.deleteConversation(id).catch(() => {})
    },

    appendMessage(sessionId: string, msg: Msg) {
      const s = this.sessions.find((x) => x.id === sessionId) || this.newSession()
      s.messages.push(msg)
      s.updatedAt = Date.now()
    },

    finalizeAssistantById(sessionId: string, msgId: string) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const m = s.messages.find((x) => x.id === msgId)
      if (m && m.role === 'assistant') {
        m.isStreaming = false
        // 收尾时务必清除「检索中 / 处理中」等瞬时状态文案，避免 SSE 已结束
        // 但 statusText 残留导致 UI 一直显示转圈（如「正在检索知识库并组织上下文…」）
        m.statusText = ''
      }
      s.updatedAt = Date.now()
    },

    appendToolCall(sessionId: string, name: string, args: any) {
      const s = this.sessions.find((x) => x.id === sessionId) || this.newSession()
      s.messages.push({
        id: uid('t'),
        role: 'tool',
        content: '',
        toolName: name,
        toolArgs: JSON.stringify(args, null, 2),
        toolStatus: 'running',
        ts: Date.now(),
      })
      s.updatedAt = Date.now()
    },

    appendToolResult(sessionId: string, result: any) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const last = s.messages[s.messages.length - 1]
      if (last && last.role === 'tool' && last.toolStatus === 'running') {
        last.toolResult = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
        last.toolStatus = 'done'
      }
      s.updatedAt = Date.now()
    },

    setAssistantStatus(sessionId: string, text: string, msgId?: string) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const m = msgId ? s.messages.find((x) => x.id === msgId) : s.messages[s.messages.length - 1]
      if (m && m.role === 'assistant') m.statusText = text
      s.updatedAt = Date.now()
    },

    setAssistantError(sessionId: string, text: string, msgId?: string) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const m = msgId ? s.messages.find((x) => x.id === msgId) : s.messages[s.messages.length - 1]
      if (m && m.role === 'assistant') {
        m.errorText = text
        m.statusText = ''
      }
      s.updatedAt = Date.now()
    },

    appendAssistantImage(sessionId: string, url: string, msgId?: string) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const m = msgId ? s.messages.find((x) => x.id === msgId) : s.messages[s.messages.length - 1]
      if (m && m.role === 'assistant') {
        ;(m.images ||= []).push(url)
        m.statusText = ''
      }
      s.updatedAt = Date.now()
    },

    appendAssistantVideo(sessionId: string, url: string, msgId?: string) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const m = msgId ? s.messages.find((x) => x.id === msgId) : s.messages[s.messages.length - 1]
      if (m && m.role === 'assistant') {
        ;(m.videos ||= []).push(url)
        m.statusText = ''
      }
      s.updatedAt = Date.now()
    },

    ensureTitle(s: Session, firstUserText: string) {
      if (s.title === '新对话' && firstUserText) {
        s.title = firstUserText.slice(0, 24)
      }
    },

    /** 将对话（标题/置顶/消息）回写到后端。剥离用户上传图 data URI 以控制体积。 */
    persistSession(s: Session) {
      const slim = s.messages.map((m) => ({
        ...m,
        image: m.image && m.image.startsWith('data:') ? '' : m.image,
      }))
      api
        .updateConversation(s.id, {
          title: s.title,
          pinned: !!s.pinned,
          messages: slim,
        })
        .catch(() => {})
    },

    // ── 附件：上传到对话框（仅落盘，解析由 agent 自主完成）───────────────────
    /** 上传文件作为用户消息：仅落盘（对标 hermes「上传只落盘」）。
     *  若附带 text，则直接把文件路径注入 prompt，让 agent 自主调 read_file 读取。 */
    async sendAttachment(file: File, opts?: { isImage?: boolean; text?: string }) {
      const s = this.ensureActive()
      const msgId = uid()
      const ext = (file.name.split('.').pop() || 'FILE').toUpperCase()
      
      // 先追加用户消息卡片（仅落盘）
      this.appendMessage(s.id, {
        id: msgId,
        role: 'user',
        content: opts?.text || '',
        ts: Date.now(),
        attachment: {
          title: file.name,
          file_type: ext,
          is_image: !!opts?.isImage,
          parsed: false,
          status: 'stored',
        },
      })
      this.ensureTitle(s, opts?.text || file.name)
      _pendingFiles.set(msgId, file)
      
      try {
        // 仅上传落盘（不解析）
        const r = await attachAndParse(file)
        this._applyAttachment(s.id, msgId, r)
        
        // 无文字的纯文件上传：不触发 stream，仅标记 stored → 用户后续补充问题后走 send()
      } catch (e: any) {
        this._setAttachmentStatus(s.id, msgId, 'error', e?.message || '上传失败')
      }
    },

    _storedPathOf(sessionId: string, msgId: string): string | null {
      const s = this.sessions.find((x) => x.id === sessionId)
      const m = s?.messages.find((x) => x.id === msgId)
      return m?.attachment?.stored_path || null
    },

    _applyAttachment(sessionId: string, msgId: string, r: any) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const m = s.messages.find((x) => x.id === msgId)
      if (!m || !m.attachment) return
      const a = m.attachment
      a.attachment_id = r.attachment_id
      a.file_id = r.file_id
      a.url = r.url
      // 仅落盘后 stored_path 不变；重解析结果不含该字段，保留原值
      if (r.stored_path !== undefined) a.stored_path = r.stored_path
      a.is_image = r.is_image
      a.file_type = r.file_type
      a.parsed = r.parsed
      a.text = r.text
      a.doc_id = r.doc_id
      a.vision_available = r.vision_available
      a.needs_plugin = r.needs_plugin
      a.text_total = r.text_total ?? null
      a.parse_error = r.parse_error
      a.status = r.parse_error
        ? 'error'
        : r.needs_plugin
          ? 'done'
          : r.parsed
            ? 'done'
            : a.stored_path
              ? 'stored'
              : 'error'
      s.updatedAt = Date.now()
    },

    _setAttachmentStatus(sessionId: string, msgId: string, status: 'parsing' | 'done' | 'error' | 'installing', err?: string) {
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      const m = s.messages.find((x) => x.id === msgId)
      if (!m || !m.attachment) return
      m.attachment.status = status
      if (err !== undefined) m.attachment.parse_error = err
      s.updatedAt = Date.now()
    },

    /** 按 stored_path 匹配会话中的附件并标记状态（解析状态透明：read_file 调用前后）。 */
    _markAttachmentByPath(sessionId: string, path: string | null, status: 'parsing' | 'done') {
      if (!path) return
      const s = this.sessions.find((x) => x.id === sessionId)
      if (!s) return
      for (const m of s.messages) {
        const a = m.attachment
        if (a?.stored_path && (a.stored_path === path || a.stored_path.endsWith(path) || path.endsWith(a.stored_path))) {
          a.status = status
        }
      }
      s.updatedAt = Date.now()
    },

    /** 扫描会话中最近 N 条消息，找出仍处于 stored（未读取）状态的附件。
     *  用于用户「先传文件、后补文字提问」场景：send() 时把路径注入 prompt，让 agent 自主读。 */
    _recentStoredAttachments(s: Session, scanCount = 8): {
      msgId: string
      stored_path: string
      title: string
      is_image: boolean
      vision_available: boolean
      url: string | null
    }[] {
      const result: {
        msgId: string
        stored_path: string
        title: string
        is_image: boolean
        vision_available: boolean
        url: string | null
      }[] = []
      const recent = s.messages.slice(-scanCount)
      for (const m of recent) {
        const a = m.attachment
        if (a?.status === 'stored' && a.stored_path) {
          result.push({
            msgId: m.id,
            stored_path: a.stored_path,
            title: a.title,
            is_image: a.is_image,
            vision_available: a.vision_available || false,
            url: a.url || null,
          })
        }
      }
      return result
    },

    async send(text: string, image?: string, doc?: { doc_id: string; title: string; file_type: string; size?: number }) {
      const content = text.trim()
      if ((!content && !image && !doc) || this.streaming) return
      const s = this.ensureActive()
      this.appendMessage(s.id, {
        id: uid(),
        role: 'user',
        content,
        ts: Date.now(),
        image: image || undefined,
        docs: doc ? [{ doc_id: doc.doc_id, title: doc.title, file_type: doc.file_type, size: doc.size }] : undefined,
      })
      this.ensureTitle(s, content || '图片对话')

      // ── Hermes 式：收集本会话最近 stored 附件，统一交给后端处置 ──
      // 后端按 hermes 方式：图片→视觉部件(native vision)；扫描件 PDF→渲染为图片喂视觉(不 OCR)；
      // 文本文档→注入路径提示让 Agent 用 read_file 自取文本。前端不再重复注入路径文本。
      const stored = this._recentStoredAttachments(s)
      const attachments: ChatAttachment[] = stored.map((a) => ({
        title: a.title,
        file_type: a.file_type || (a.is_image ? 'IMAGE' : 'FILE'),
        is_image: a.is_image,
        url: a.url,
        stored_path: a.stored_path,
        vision_available: a.vision_available,
      }))
      // 附件状态透明：标记 parsing（图片/扫描件由后端直接喂视觉，结束后标记 done）
      for (const a of stored) {
        this._setAttachmentStatus(s.id, a.msgId, 'parsing')
      }

      // 仅保留「图生图/图生视频」用的显式参考图（与附件视觉输入是两回事，互不干扰）
      let promptText = content
      if (!promptText && attachments.length) {
        promptText = '请阅读并总结以上上传的文件内容。'
      }

      // 无任何内容、无附件、无参考图 → 跳过（避免无意义请求）
      if (!attachments.length && !content && !image) return

      await this._stream(s, promptText, { image, attachments })

      // 流式结束后：仍停留在 parsing 的附件（模型未显式 read_file，如图片/扫描件已由
      // 后端直接喂视觉）标记为 done，避免卡片卡在「解析中」。
      for (const a of stored) {
        const m = s.messages.find((x) => x.id === a.msgId)
        if (m?.attachment && m.attachment.status === 'parsing') {
          this._setAttachmentStatus(s.id, a.msgId, 'done')
        }
      }
    },

    /** 流式对话核心：仅生成助手回复并追加工具/委派事件（不创建用户消息）。 */
    async _stream(
      s: Session,
      content: string,
      opts?: { image?: string; attachments?: ChatAttachment[] },
    ) {
      const app = useAppStore()
      if (this.isSessionStreaming(s.id)) return
      this.error = ''
      const ctrl = new AbortController()
      _sessionAborts.set(s.id, ctrl)
      // 委派产生的子智能体气泡 id（用于把子智能体 token 路由到独立气泡）
      let subId: string | null = null
      // read_file 按需解析：记录当前正在 read 的附件路径，用于解析状态透明
      let readPath: string | null = null
      const mainId = uid()
      this.appendMessage(s.id, {
        id: mainId,
        role: 'assistant',
        content: '',
        ts: Date.now(),
        isStreaming: true,
      })
      const mainMsg = s.messages.find((m) => m.id === mainId)
      // 主智能体「委派推理链」：随 SSE 事件逐步累积，作为思维图谱·推理步骤的数据源。
      // 关键：必须经由响应式代理（mainMsg.agentTrace）push，面板 computed 才能实时刷新；
      // 若改闭包内的普通数组，底层数据虽变但 Vue 不会触发重算。
      if (mainMsg) mainMsg.agentTrace = []
      // 委派关系栈：随 SSE 事件累积，作为思维图谱·概念图谱「协作调用图」的数据源。
      // 参照验证脚本的栈推导：delegate_start 入栈、delegate_end 出栈，
      // 入栈前的栈顶即本次委派的「调用方（谁发出）」；栈空时为顶层主管/所选智能体。
      if (mainMsg) mainMsg.agentDelegations = []
      const openDeleg: AgentDelegation[] = []
      const delegRoot = () => this.selectedAgent || '主管(顶层)'
      let _delegIdx = 0
      const pushDeleg = (d: AgentDelegation) => {
        const arr = mainMsg?.agentDelegations
        if (arr) {
          arr.push(d)
          // 保存响应式代理引用，便于 delegate_end 时就地更新状态触发刷新
          d = arr[arr.length - 1]
        }
        return d
      }
      const derivStatus = (result: string): AgentDelegation['status'] => {
        const r = result || ''
        if (r.includes('[委派超时]')) return 'timeout'
        if (r.includes('[委派失败]') || r.includes('[子智能体错误]')) return 'error'
        if (!r.trim()) return 'empty'
        return 'done'
      }
      let _traceIdx = 0
      const pushTrace = (st: Omit<AgentTraceStep, 'id' | 'index'>) => {
        const step: AgentTraceStep = { id: `tr_${_traceIdx}`, index: ++_traceIdx, ...st }
        if (mainMsg) (mainMsg.agentTrace ||= []).push(step)
        s.updatedAt = Date.now()
      }
      // 以用户原始诉求作为「目标」步骤，让推理链有起点
      if (content.trim()) {
        pushTrace({ kind: 'goal', depth: 0, text: '目标：' + content.trim().slice(0, 80) })
      }
      const summarize = (t: string, n = 80) => {
        const c = (t || '').replace(/\s+/g, ' ').trim()
        return c.length > n ? c.slice(0, n) + '…' : c
      }
      const findMsg = (id: string) => s.messages.find((m) => m.id === id)
      const appendText = (id: string, delta: string) => {
        const m = findMsg(id)
        if (m) {
          m.content += delta
          m.isStreaming = true
          s.updatedAt = Date.now()
        }
      }

      // ── 无数据超时守护：120s 内无任何事件 → 判定模型无响应，中止并报错 ──
      // 防止 streamChat fetch 永久挂起导致本会话 isStreaming 卡在 true，阻断该会话后续发送
      let idleTimer: ReturnType<typeof setTimeout> | null = null
      const IDLE_MS = 120_000
      const resetIdle = () => {
        if (idleTimer) clearTimeout(idleTimer)
        idleTimer = setTimeout(() => {
          console.warn('[stream] 无数据超时(120s)，强制中止')
          ctrl.abort()
        }, IDLE_MS)
      }
      resetIdle()

      try {
        await streamChat(
          {
            message: content,
            session: s.id,
            model: app.selectedModel || undefined,
            image: opts?.image || undefined,
            agent: this.selectedAgent || undefined,
            attachments: opts?.attachments || undefined,
          },
          (ev) => {
            resetIdle() // 每收到一个事件就重置空闲计时器
            if (ev.type === 'token') {
              // 首个 token 到达即代表上下文检索已完成、正式开始生成，
              // 立即清除「检索中…」等瞬时状态提示，避免持续转圈
              this.setAssistantStatus(s.id, '', mainId)
              if (ev.agent) {
                // 子智能体输出 → 独立气泡
                if (!subId) {
                  subId = uid()
                  this.appendMessage(s.id, { id: subId, role: 'assistant', content: '', ts: Date.now(), agent: ev.agent, isStreaming: true })
                }
                appendText(subId, ev.text || '')
              } else {
                // 主管输出 → 固定在主气泡（mainId），避免串到子智能体气泡
                appendText(mainId, ev.text || '')
              }
            } else if (ev.type === 'status') {
              this.setAssistantStatus(s.id, ev.text || '', mainId)
            } else if (ev.type === 'image') {
              if (ev.url) this.appendAssistantImage(s.id, ev.url, mainId)
            } else if (ev.type === 'video') {
              if (ev.url) this.appendAssistantVideo(s.id, ev.url, mainId)
            } else if (ev.type === 'error') {
              // 子智能体执行出错也记入推理链（深度 1，备注），让图谱如实反映失败分支；
              // 主管自身出错才标记整条消息为失败（由 Orchestrator 兜底时不污染主消息）。
              if (ev.agent) {
                pushTrace({ kind: 'note', depth: 1, agent: ev.agent, text: `${ev.agent} 出错：${summarize(ev.message || '生成失败', 70)}` })
              } else {
                this.setAssistantError(s.id, ev.message || '生成失败', mainId)
              }
            } else if (ev.type === 'tool_call') {
              // delegate_to_agent 由专属的 delegate_start / delegate_end 渲染委派卡片，
              // 此处不再生成通用工具气泡，否则该气泡无对应 tool_result 收口会永远停在 running。
              if (ev.name !== 'delegate_to_agent') {
                this.appendToolCall(s.id, ev.name, ev.args)
              }
              // 委派推理链：子智能体的工具调用作为「行动」步骤（深度 1）。
              // 主管自身的 delegate_to_agent 由 delegate_start 记录，此处跳过避免重复。
              if (ev.agent && ev.name !== 'delegate_to_agent') {
                pushTrace({ kind: 'action', depth: 1, agent: ev.agent, text: `${ev.agent} 调用 ${ev.name}` })
              }
              // 解析状态透明：模型开始用 read_file 读取某附件 → 该附件进入「解析中」
              if (ev.name === 'read_file' && ev.args?.path) {
                readPath = ev.args.path
                this._markAttachmentByPath(s.id, readPath, 'parsing')
              }
            } else if (ev.type === 'tool_result') {
              this.appendToolResult(s.id, ev.result)
              // read_file 返回 → 该附件解析完成（内容已在工具气泡内），清除「解析中」
              if (readPath) {
                this._markAttachmentByPath(s.id, readPath, 'done')
                readPath = null
              }
            } else if (ev.type === 'run_failed') {
              // run.failed 静默吞错检测（对标 hermes）：工具/解析失败不打断对话，
              // 仅在控制台记录，不向用户展示红色错误。
              console.warn('[run.failed]', ev.name, ev.message)
            } else if (ev.type === 'delegate_start') {
              this.appendToolCall(s.id, '委派 ▸ ' + (ev.agent || ''), { task: ev.task || '' })
              // 结构化记录委派关系：栈顶即「调用方（谁发出）」，栈空时为顶层主管/所选智能体
              const _callee = ev.agent || '未知智能体'
              const _caller = openDeleg.length ? openDeleg[openDeleg.length - 1].callee : delegRoot()
              const _d: AgentDelegation = {
                id: `del_${_delegIdx++}`,
                caller: _caller,
                callee: _callee,
                task: ev.task || '',
                result: '',
                status: 'running',
                startedAt: Date.now(),
              }
              openDeleg.push(pushDeleg(_d))
              // 主智能体「调度」动作：记为推理链深度 0 步骤
              pushTrace({ kind: 'action', depth: 0, agent: ev.agent || undefined, text: `委派 ${ev.agent || '子智能体'}：${summarize(ev.task || '', 70)}` })
            } else if (ev.type === 'delegate_end') {
              this.appendToolResult(s.id, ev.result || '(无返回)')
              // 关闭对应的委派关系（栈顶优先，否则按 callee 查找），更新状态/结果
              const _endCallee = ev.agent || ''
              let _i = openDeleg.length - 1
              while (_i >= 0 && openDeleg[_i].callee !== _endCallee) _i--
              if (_i >= 0) {
                const _d = openDeleg.splice(_i, 1)[0]
                _d.result = ev.result || ''
                _d.endedAt = Date.now()
                _d.status = derivStatus(ev.result || '')
              }
              // 子智能体返回 → 推理链「观察」步骤（深度 1，归属该子智能体）
              if (ev.agent) {
                pushTrace({ kind: 'observe', depth: 1, agent: ev.agent, text: `${ev.agent} 返回（${String(ev.result || '').length} 字）：${summarize(ev.result || '', 70)}` })
              }
              if (subId) {
                const m = findMsg(subId)
                if (m) m.isStreaming = false
                subId = null
              }
            } else if (ev.type === 'done') {
              this.finalizeAssistantById(s.id, mainId)
              if (subId) {
                const m = findMsg(subId)
                if (m) m.isStreaming = false
                subId = null
              }
            }
          },
          ctrl.signal,
          resetIdle, // 任意网络字节（含 SSE 心跳）到达即重置空闲计时器，防止长任务误判断流
        )
      } catch (e: any) {
        const msg = e?.message || '对话失败'
        // 用户主动「停止」触发 AbortError，不当作错误
        if (e?.name === 'AbortError' || e?.message === 'The user aborted a request.') {
          /* 主动停止，不报错 */
        } else {
          this.error = msg
          this.setAssistantError(s.id, msg, mainId)
        }
      } finally {
        if (idleTimer) clearTimeout(idleTimer)
        _sessionAborts.delete(s.id)
        // 兜底：主气泡与子智能体气泡均结束打字态，避免卡在「打字中」
        this.finalizeAssistantById(s.id, mainId)
        if (subId) this.finalizeAssistantById(s.id, subId)
        this.persistSession(s)
      }
    },

    stop(id?: string) {
      const sid = id || this.activeId
      const ctrl = _sessionAborts.get(sid)
      if (ctrl) {
        ctrl.abort()
        _sessionAborts.delete(sid)
      }
      const s = this.sessions.find((x) => x.id === sid)
      if (s) {
        for (const m of s.messages) {
          if (m.role === 'assistant' && m.isStreaming) m.isStreaming = false
        }
      }
    },

    /** 切换账号时清空内存态（数据在服务端按用户隔离）。 */
    reset() {
      _sessionAborts.clear()
      this.sessions = []
      this.activeId = ''
      this.error = ''
    },
  },
})
