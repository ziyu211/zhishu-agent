/**
 * 模型 / Provider 相关 API。
 */
import { request } from './http'
import type { ModelsResp, ProvidersResp, Provider, RemoteModelsResp } from './types'

export const listModels = () => request<ModelsResp>('/api/v1/models')
export const listPresets = () => request<any>('/api/v1/models/presets')
export const listProviders = () => request<ProvidersResp>('/api/v1/providers')
export const addProvider = (body: Partial<Provider>) =>
  request<any>('/api/v1/providers', { method: 'POST', body })
export const updateProvider = (name: string, body: Partial<Provider>) =>
  request<any>(`/api/v1/providers/${encodeURIComponent(name)}`, { method: 'PUT', body })
export const deleteProvider = (name: string) =>
  request<any>(`/api/v1/providers/${encodeURIComponent(name)}`, { method: 'DELETE' })
export const setDefaultModel = (model: string) =>
  request<any>('/api/v1/models/default', { method: 'POST', body: { model } })
export const fetchRemoteModels = (base_url: string, api_key: string) =>
  request<RemoteModelsResp>('/api/v1/models/fetch', { method: 'POST', body: { base_url, api_key } })

export const modelsApi = {
  listModels,
  listPresets,
  listProviders,
  addProvider,
  updateProvider,
  deleteProvider,
  setDefaultModel,
  fetchRemoteModels,
}
