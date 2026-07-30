/**
 * 智枢智能体 —— 共享 HTTP 核心
 * 对标 hermes-web-ui 的 api/client.ts：统一的 fetch 封装、鉴权(token 存 localStorage)、
 * 401 跳登录、离散提示，以及可配置 baseURL（VITE_API_BASE）。
 * 所有按域拆分的 api 模块都从这里导入 `request`。
 */
import { createDiscreteApi } from 'naive-ui'
import { getActAs } from './actas'

// ─── 存储键 ───────────────────────────────────────────────
export const TOKEN_KEY = 'zhishu_token'
export const USER_KEY = 'zhishu_user'

// ─── 环境 baseURL（默认同源 '/api/v1'） ──────────────────
// 通过 .env 配置 VITE_API_BASE 可指向独立后端，例如 http://host:8080
const BASE_URL: string = (import.meta.env.VITE_API_BASE as string | undefined) || ''

export function resolveUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path
  return BASE_URL + path
}

// ─── 鉴权 / 用户 ─────────────────────────────────────────
export interface LocalUser {
  user: string
  role: string
  role_label?: string
  display_name?: string
  perms?: string[]
  [k: string]: unknown
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
export function hasToken(): boolean {
  return !!getToken()
}
export function saveUser(u: LocalUser) {
  localStorage.setItem(USER_KEY, JSON.stringify(u))
}
export function getUser(): LocalUser | null {
  const v = localStorage.getItem(USER_KEY)
  return v ? (JSON.parse(v) as LocalUser) : null
}

// ─── 请求封装 ───────────────────────────────────────────
export async function request<T = any>(
  path: string,
  opts: {
    method?: string
    body?: any
    headers?: Record<string, string>
    formData?: FormData
    signal?: AbortSignal
  } = {},
): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers || {}) }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  // 管理员代管：携带 X-Act-As，使后端以目标用户身份执行（查看/配置其私有模块）
  const actAsUser = getActAs()
  if (actAsUser) headers['X-Act-As'] = actAsUser
  let body: any = undefined
  if (opts.formData) {
    body = opts.formData
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  const resp = await fetch(resolveUrl(path), {
    method: opts.method || 'GET',
    headers,
    body,
    signal: opts.signal,
  })
  if (resp.status === 401) {
    clearToken()
    if (!location.hash.replace('#', '').startsWith('/login')) {
      location.hash = '#/login'
    }
    throw new Error('未登录或登录已过期')
  }
  if (!resp.ok) {
    let msg = `请求失败(${resp.status})`
    try {
      const d = await resp.json()
      msg = d.detail || d.msg || msg
    } catch {}
    throw new Error(msg)
  }
  if (resp.status === 204) return undefined as T
  const ct = resp.headers.get('content-type') || ''
  if (ct.includes('application/json')) return (await resp.json()) as T
  return (await resp.text()) as unknown as T
}

// ─── 文件下载（blob → 触发浏览器下载） ───────────────────
export async function downloadFile(path: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const actAsUser = getActAs()
  if (actAsUser) headers['X-Act-As'] = actAsUser
  const resp = await fetch(resolveUrl(path), { method: 'GET', headers })
  if (!resp.ok) {
    let msg = `下载失败(${resp.status})`
    try {
      const d = await resp.json()
      msg = d.detail || d.msg || msg
    } catch {}
    throw new Error(msg)
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// ─── 离散提示（不依赖组件树内的 provider） ───────────────
let _discrete: ReturnType<typeof createDiscreteApi> | null = null
function discrete() {
  if (!_discrete) _discrete = createDiscreteApi(['message', 'dialog', 'notification'])
  return _discrete
}
export function toast(
  type: 'success' | 'error' | 'info' | 'warning',
  content: string,
  duration = 2500,
) {
  ;(discrete().message as any)[type](content, { duration })
}
export function confirmDialog(opts: { title: string; content: string }): Promise<boolean> {
  return new Promise((resolve) => {
    discrete().dialog.info({
      title: opts.title,
      content: opts.content,
      positiveText: '确定',
      negativeText: '取消',
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
    })
  })
}
