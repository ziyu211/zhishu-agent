/**
 * 模型 / Provider store。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listModels,
  listProviders,
  addProvider as apiAdd,
  updateProvider as apiUpdate,
  deleteProvider as apiDelete,
  setDefaultModel as apiSetDefault,
  fetchRemoteModels as apiFetchRemote,
} from '@/api/models'
import { toast } from '@/api/http'
import type { ModelGroup, Provider, RemoteModelsResp } from '@/api/types'

export const useModelsStore = defineStore('models', () => {
  const groups = ref<ModelGroup[]>([]) // 供模型选择器（provider/models 结构）
  const providers = ref<Provider[]>([]) // 供 Provider 管理
  const defaultModel = ref('')
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      const r = await listModels()
      groups.value = r.providers || []
      defaultModel.value = r.default_model || ''
    } catch (e: any) {
      toast('error', e?.message || '加载模型失败')
    } finally {
      loading.value = false
    }
  }
  async function loadProviders() {
    try {
      providers.value = (await listProviders()).providers || []
    } catch (e: any) {
      toast('error', e?.message || '加载 Provider 失败')
    }
  }
  async function add(body: Partial<Provider>) {
    await apiAdd(body)
    toast('success', '已添加 Provider')
    await loadProviders()
  }
  async function update(name: string, body: Partial<Provider>) {
    await apiUpdate(name, body)
    toast('success', '已更新 Provider')
    await loadProviders()
  }
  async function remove(name: string) {
    await apiDelete(name)
    toast('success', '已删除 Provider')
    await loadProviders()
  }
  async function setDefault(model: string) {
    await apiSetDefault(model)
    defaultModel.value = model
  }
  async function fetchRemote(base_url: string, api_key: string): Promise<string[]> {
    const r = await apiFetchRemote(base_url, api_key)
    return r.models || []
  }

  return {
    groups,
    providers,
    defaultModel,
    loading,
    load,
    loadProviders,
    add,
    update,
    remove,
    setDefault,
    fetchRemote,
  }
})
