/**
 * 管理 / 审计 API。
 */
import { request } from './http'
import type { AdminStatus, AuditResp } from './types'

export const adminStatus = () => request<AdminStatus>('/api/v1/admin/status', { skipActAs: true })
export const adminAudit = (limit = 100) =>
  request<AuditResp>(`/api/v1/admin/audit?limit=${limit}`, { skipActAs: true })
export const adminRedact = (text: string) =>
  request<{ enabled: boolean; result: string }>('/api/v1/admin/redact', { method: 'POST', body: { text }, skipActAs: true })

export const systemApi = { adminStatus, adminAudit, adminRedact }
