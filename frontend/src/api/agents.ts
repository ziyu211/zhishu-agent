/**
 * 子智能体（多 Agent 协作成员）API。
 */
import { request } from './http'
import type { AgentItem, AgentDetail, AgentOptionsResp } from './types'

export const listAgents = () => request<AgentItem[]>('/api/v1/agents')
export const getAgent = (name: string) =>
  request<AgentDetail>(`/api/v1/agents/${encodeURIComponent(name)}`)
export const createAgent = (body: any) => request<any>('/api/v1/agents', { method: 'POST', body })
export const updateAgent = (name: string, body: any) =>
  request<any>(`/api/v1/agents/${encodeURIComponent(name)}`, { method: 'PUT', body })
export const deleteAgent = (name: string) =>
  request<any>(`/api/v1/agents/${encodeURIComponent(name)}`, { method: 'DELETE' })
export const toggleAgent = (name: string, enabled: boolean) =>
  request<any>(`/api/v1/agents/${encodeURIComponent(name)}/toggle`, {
    method: 'PUT',
    body: { enabled },
  })
export const agentOptions = () => request<AgentOptionsResp>('/api/v1/agents/options')

export const agentsApi = {
  listAgents,
  getAgent,
  createAgent,
  updateAgent,
  deleteAgent,
  toggleAgent,
  agentOptions,
}
