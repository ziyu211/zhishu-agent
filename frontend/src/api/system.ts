/**
 * 管理 / 审计 API。
 */
import { request } from './http'
import type { AdminStatus, AuditItem } from './types'

export const adminStatus = () => request<AdminStatus>('/api/v1/admin/status')
export const adminAudit = (limit = 100) =>
  request<AuditItem[]>(`/api/v1/admin/audit?limit=${limit}`)

export const systemApi = { adminStatus, adminAudit }
