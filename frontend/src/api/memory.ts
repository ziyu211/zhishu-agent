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

export const memoryApi = { getMemory, saveMemory, exportMemory, searchMemory }
