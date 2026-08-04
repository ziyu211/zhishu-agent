/**
 * 对话 / 会话 + 流式对话（SSE）。
 */
import { request, getToken, resolveUrl } from './http'
import { getActAs } from './actas'
import type {
  Conversation,
  ConversationListResp,
  ConversationCreateReq,
  ChatEvent,
  AttachParseResp,
} from './types'

export const listConversations = (scope: 'mine' | 'all' = 'mine') =>
  request<ConversationListResp>(`/api/v1/conversations?scope=${scope}`)
export const createConversation = (body: ConversationCreateReq) =>
  request<any>('/api/v1/conversations', { method: 'POST', body })
export const getConversation = (cid: string) =>
  request<Conversation>(`/api/v1/conversations/${encodeURIComponent(cid)}`)
export const updateConversation = (cid: string, body: any) =>
  request<any>(`/api/v1/conversations/${encodeURIComponent(cid)}`, { method: 'PUT', body })
export const deleteConversation = (cid: string) =>
  request<any>(`/api/v1/conversations/${encodeURIComponent(cid)}`, { method: 'DELETE' })

export const chatApi = {
  listConversations,
  createConversation,
  getConversation,
  updateConversation,
  deleteConversation,
}

/** 上传附件到对话框：仅落盘不解析（对标 hermes「上传只落盘」）。
 *  返回 stored_path / url / status=stored，解析由 read_file 工具按需触发。 */
export const attachAndParse = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  // 仅上传，超时兜底（后端不再做阻塞式解析）
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 60_000)
  return request<AttachParseResp>('/api/v1/chat/attach', {
    method: 'POST',
    formData: fd,
    signal: ctrl.signal,
  })
    .catch((err) => {
      if (err?.name === 'AbortError') {
        throw new Error('上传超时未返回（60s），请检查文件是否过大或网络异常')
      }
      throw err
    })
    .finally(() => clearTimeout(timer))
}

/** 解析已落盘的附件：按 stored_path 直接提取文字（文档/文本零依赖提取），
 *  返回 { parsed, text, text_total, doc_id, parse_error, status }，
 *  供 sendAttachment 在用户附带操作要求时直接把内容喂给模型。 */
export const parseAttachment = (storedPath: string) => {
  const fd = new FormData()
  fd.append('path', storedPath)
  return request<any>('/api/v1/chat/parse', { method: 'POST', formData: fd })
}

/**
 * 流式对话：POST /api/v1/chat，通过 fetch ReadableStream 读取 SSE。
 * onEvent 收到后端推送的事件对象 ChatEvent。
 */
export interface ChatAttachment {
  title?: string
  file_type?: string
  is_image?: boolean
  url?: string | null
  stored_path?: string | null
  vision_available?: boolean
}
export async function streamChat(
  payload: {
    message: string
    session?: string
    model?: string
    image?: string
    agent?: string
    attachments?: ChatAttachment[]
  },
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
  /** 任意网络字节到达即触发（含 SSE 心跳 `: ping` 注释行）——用于重置前端空闲计时器，
   *  避免后端「长思考 / 长工具循环」期间无业务 data 事件导致空闲超时误判断流。 */
  onActivity?: () => void,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  // 管理员代管（X-Act-As）：发消息也必须携带，否则会穿透代管以 admin 身份执行，
  // 导致会话归属错乱、审计留痕失真。
  const act = getActAs()
  if (act) headers['X-Act-As'] = act
  const resp = await fetch(resolveUrl('/api/v1/chat'), {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal,
  })
  if (!resp.ok) {
    let msg = `对话请求失败(${resp.status})`
    try {
      const d = await resp.json()
      msg = d.detail || msg
    } catch {}
    throw new Error(msg)
  }
  if (!resp.body) return
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    // 收到任意字节（含心跳注释行）都视为连接活跃 —— 即便不是可解析的 data: 业务事件，
    // 也能让上层空闲计时器保持复位，防止「后端正在生成但最终回答尚未首 token」时
    // 前端误判断流而中止（表现为「对话不出结果，需重发」）。
    onActivity?.()
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() || ''
    for (const line of lines) {
      const t = line.trim()
      if (!t.startsWith('data:')) continue
      const data = t.slice(5).trim()
      if (!data || data === '[DONE]') continue
      try {
        onEvent(JSON.parse(data) as ChatEvent)
      } catch {}
    }
  }
}
