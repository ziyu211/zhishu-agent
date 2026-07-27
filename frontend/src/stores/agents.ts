/**
 * 子智能体 store（多 Agent 协作成员）。
 * 对标 hermes-web-ui 的 stores/hermes/*.ts：每域一个文件，状态 = 域数据 + loading + fetch action。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listAgents,
  getAgent,
  createAgent as apiCreate,
  updateAgent as apiUpdate,
  deleteAgent as apiDelete,
  toggleAgent as apiToggle,
  agentOptions,
} from '@/api/agents'
import { toast } from '@/api/http'
import type { AgentItem, AgentOptionsResp } from '@/api/types'

export const useAgentsStore = defineStore('agents', () => {
  const agents = ref<AgentItem[]>([])
  const loading = ref(false)
  const options = ref<AgentOptionsResp['agents']>([])

  async function load() {
    loading.value = true
    try {
      agents.value = await listAgents()
    } catch (e: any) {
      toast('error', e?.message || '加载智能体失败')
    } finally {
      loading.value = false
    }
  }
  async function loadOptions() {
    try {
      options.value = (await agentOptions()).agents
    } catch {
      /* noop */
    }
  }
  async function create(body: any) {
    await apiCreate(body)
    toast('success', '已创建智能体')
    await load()
  }
  async function update(name: string, body: any) {
    await apiUpdate(name, body)
    toast('success', '已更新智能体')
    await load()
  }
  async function remove(name: string) {
    await apiDelete(name)
    toast('success', '已删除')
    await load()
  }
  async function toggle(name: string, enabled: boolean) {
    await apiToggle(name, enabled)
    await load()
  }

  return { agents, loading, options, load, loadOptions, create, update, remove, toggle }
})
