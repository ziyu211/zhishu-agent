/**
 * 插件 Plugins store。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listPlugins,
  getPlugin,
  createPlugin as apiCreate,
  updatePlugin as apiUpdate,
  deletePlugin as apiDelete,
  togglePlugin as apiToggle,
  refreshPlugins as apiRefresh,
} from '@/api/plugins'
import { toast } from '@/api/http'
import type { PluginItem } from '@/api/types'

export const usePluginsStore = defineStore('plugins', () => {
  const plugins = ref<PluginItem[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      plugins.value = (await listPlugins()).plugins || []
    } catch (e: any) {
      toast('error', e?.message || '加载插件失败')
    } finally {
      loading.value = false
    }
  }
  async function get(name: string) {
    return getPlugin(name)
  }
  async function create(body: any) {
    await apiCreate(body)
    toast('success', '已创建插件')
    await load()
  }
  async function update(name: string, body: any) {
    await apiUpdate(name, body)
    toast('success', '已更新插件')
    await load()
  }
  async function remove(name: string) {
    await apiDelete(name)
    toast('success', '已删除插件')
    await load()
  }
  async function toggle(name: string, enabled: boolean) {
    await apiToggle(name, enabled)
    await load()
  }
  async function refresh() {
    await apiRefresh()
    toast('success', '已刷新插件')
    await load()
  }

  return { plugins, loading, load, get, create, update, remove, toggle, refresh }
})
