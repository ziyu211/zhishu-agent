/**
 * 记忆 Memory store。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getMemory, saveMemory as apiSave, exportMemory, searchMemory } from '@/api/memory'
import { toast } from '@/api/http'
import type { MemoryData, MemoryExport } from '@/api/types'

export const useMemoryStore = defineStore('memory', () => {
  const data = ref<MemoryData>({})
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      data.value = (await getMemory()) || {}
    } catch (e: any) {
      toast('error', e?.message || '加载记忆失败')
    } finally {
      loading.value = false
    }
  }
  async function save(body: MemoryData) {
    await apiSave(body)
    data.value = { ...data.value, ...body }
    toast('success', '记忆已保存')
  }
  async function exportData(): Promise<MemoryExport | null> {
    try {
      return await exportMemory()
    } catch (e: any) {
      toast('error', e?.message || '导出失败')
      return null
    }
  }
  async function search(q: string) {
    try {
      return await searchMemory(q)
    } catch {
      return null
    }
  }

  return { data, loading, load, save, exportData, search }
})
