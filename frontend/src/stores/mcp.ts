/**
 * MCP 服务器 store。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listMcp,
  getMcp,
  createMcp as apiCreate,
  updateMcp as apiUpdate,
  deleteMcp as apiDelete,
  toggleMcp as apiToggle,
  refreshMcp as apiRefresh,
  connectMcp as apiConnect,
  callMcp as apiCall,
  listTools,
} from '@/api/mcp'
import { toast } from '@/api/http'
import type { McpItem, ToolItem } from '@/api/types'

export const useMcpStore = defineStore('mcp', () => {
  const servers = ref<McpItem[]>([])
  const tools = ref<ToolItem[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      servers.value = (await listMcp()).servers || []
    } catch (e: any) {
      toast('error', e?.message || '加载 MCP 失败')
    } finally {
      loading.value = false
    }
  }
  async function loadTools() {
    try {
      tools.value = (await listTools()).tools || []
    } catch {
      /* noop */
    }
  }
  async function get(name: string) {
    return getMcp(name)
  }
  async function create(body: any) {
    await apiCreate(body)
    toast('success', '已创建 MCP 服务器')
    await load()
  }
  async function update(name: string, body: any) {
    await apiUpdate(name, body)
    toast('success', '已更新 MCP 服务器')
    await load()
  }
  async function remove(name: string) {
    await apiDelete(name)
    toast('success', '已删除 MCP 服务器')
    await load()
  }
  async function toggle(name: string, enabled: boolean) {
    await apiToggle(name, enabled)
    await load()
  }
  async function refresh() {
    await apiRefresh()
    toast('success', '已刷新 MCP')
    await load()
  }
  async function connect(name: string) {
    await apiConnect(name)
    toast('success', '已连接 ' + name)
    await load()
  }
  async function call(name: string, tool: string, args: any) {
    return apiCall(name, tool, args)
  }

  return { servers, tools, loading, load, loadTools, get, create, update, remove, toggle, refresh, connect, call }
})
