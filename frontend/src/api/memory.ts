/**
 * 记忆 Memory API。
 */
import { request } from './http'
import type { MemoryData, MemoryExport } from './types'

export const getMemory = () => request<MemoryData>('/api/v1/memory')
export const saveMemory = (body: MemoryData) =>
  request<any>('/api/v1/memory', { method: 'PUT', body })
export const exportMemory = () => request<MemoryExport>('/api/v1/memory/export')
export const searchMemory = (q: string) =>
  request<any>(`/api/v1/memory/search?q=${encodeURIComponent(q)}`)
/** 向量长期记忆体量（非管理员仅返回自己的；管理员可查全部） */
export const getVectorStats = () => request<any>('/api/v1/memory/vector')
/** 清空向量长期记忆（非管理员仅能清空自己的；管理员可传 owner 清空任意用户或全部） */
export const clearVectorMemory = (owner?: string) =>
  request<any>(`/api/v1/memory/vector${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`, { method: 'DELETE' })

export const memoryApi = { getMemory, saveMemory, exportMemory, searchMemory, getVectorStats, clearVectorMemory }
