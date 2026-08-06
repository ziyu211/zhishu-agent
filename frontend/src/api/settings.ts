/**
 * 运行时设置 API（长期记忆等 opt-in 特性的自助开关）。
 */
import { request } from './http'
import type { SettingsResp } from './types'

export const getSettings = () =>
  request<SettingsResp>('/api/v1/settings', { skipActAs: true })

export const updateSettings = (body: {
  memory?: { vector_enabled?: boolean; vector_top_k?: number }
  security?: Partial<{
    allow_private_fetch: boolean
    outbound_allow: boolean
    allow_code_exec: boolean
    allow_shell: boolean
    shell_enforce_allowlist: boolean
    enable_audit: boolean
    enable_redact: boolean
  }>
}) => request<SettingsResp>('/api/v1/settings', { method: 'POST', body, skipActAs: true })

export const settingsApi = { getSettings, updateSettings }
