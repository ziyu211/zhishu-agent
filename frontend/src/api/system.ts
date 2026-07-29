/**
 * 管理 / 审计 API。
 */
import { request } from './http'
import type { AdminStatus, AuditResp } from './types'

export const adminStatus = () => request<AdminStatus>('/api/v1/admin/status')
export const adminAudit = (limit = 100) =>
  request<AuditResp>(`/api/v1/admin/audit?limit=${limit}`)
export const adminRedact = (text: string) =>
  request<{ enabled: boolean; result: string }>('/api/v1/admin/redact', { method: 'POST', body: { text } })

export const systemApi = { adminStatus, adminAudit, adminRedact }
