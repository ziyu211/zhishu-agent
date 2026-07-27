/**
 * MCP 服务器 API（含已注册工具清单 listTools）。
 */
import { request } from './http'
import type { McpItem, ToolItem } from './types'

export const listMcp = () => request<McpItem[]>('/api/v1/mcp')
export const getMcp = (name: string) =>
  request<McpItem>(`/api/v1/mcp/${encodeURIComponent(name)}`)
export const createMcp = (body: any) => request<any>('/api/v1/mcp', { method: 'POST', body })
export const updateMcp = (name: string, body: any) =>
  request<any>(`/api/v1/mcp/${encodeURIComponent(name)}`, { method: 'PUT', body })
export const deleteMcp = (name: string) =>
  request<any>(`/api/v1/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' })
export const toggleMcp = (name: string, enabled: boolean) =>
  request<any>(`/api/v1/mcp/${encodeURIComponent(name)}/toggle`, {
    method: 'PUT',
    body: { enabled },
  })
export const refreshMcp = () => request<any>('/api/v1/mcp/refresh', { method: 'POST' })
export const connectMcp = (name: string) =>
  request<any>(`/api/v1/mcp/${encodeURIComponent(name)}/connect`, { method: 'POST' })
export const callMcp = (name: string, tool: string, args: any) =>
  request<any>(`/api/v1/mcp/${encodeURIComponent(name)}/call`, {
    method: 'POST',
    body: { tool, arguments: args },
  })
export const listTools = () => request<ToolItem[]>('/api/v1/tools')

export const mcpApi = {
  listMcp,
  getMcp,
  createMcp,
  updateMcp,
  deleteMcp,
  toggleMcp,
  refreshMcp,
  connectMcp,
  callMcp,
  listTools,
}
