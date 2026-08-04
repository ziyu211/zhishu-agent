/**
 * 运行时设置 API（长期记忆等 opt-in 特性的自助开关）。
 */
import { request } from './http'
import type { SettingsResp } from './types'

export const getSettings = () =>
  request<SettingsResp>('/api/v1/settings', { skipActAs: true })

export const updateSettings = (body: {
  memory?: { vector_enabled?: boolean; vector_top_k?: number }
}) => request<SettingsResp>('/api/v1/settings', { method: 'POST', body, skipActAs: true })

export const settingsApi = { getSettings, updateSettings }
