/**
 * 插件 Plugins API。
 */
import { request } from './http'
import type { PluginItem, PluginsResp } from './types'

export const listPlugins = () => request<PluginsResp>('/api/v1/plugins')
export const getPlugin = (name: string) =>
  request<PluginItem>(`/api/v1/plugins/${encodeURIComponent(name)}`)
export const createPlugin = (body: any) => request<any>('/api/v1/plugins', { method: 'POST', body })
export const updatePlugin = (name: string, body: any) =>
  request<any>(`/api/v1/plugins/${encodeURIComponent(name)}`, { method: 'PUT', body })
export const deletePlugin = (name: string) =>
  request<any>(`/api/v1/plugins/${encodeURIComponent(name)}`, { method: 'DELETE' })
export const togglePlugin = (name: string, enabled: boolean) =>
  request<any>(`/api/v1/plugins/${encodeURIComponent(name)}/toggle`, {
    method: 'PUT',
    body: { enabled },
  })
export const refreshPlugins = () => request<any>('/api/v1/plugins/refresh', { method: 'POST' })
/** 按需安装解析插件（用户在前端确认后调用，实现「直接安装」）。 */
export const installPlugin = (name: string, descriptor?: any) =>
  request<any>('/api/v1/plugins/install', {
    method: 'POST',
    body: { name, descriptor: descriptor || null },
  })

export const pluginsApi = {
  listPlugins,
  getPlugin,
  createPlugin,
  updatePlugin,
  deletePlugin,
  togglePlugin,
  refreshPlugins,
  installPlugin,
}
