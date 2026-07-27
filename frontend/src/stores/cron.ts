/**
 * 定时任务 store。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listCron,
  createCron as apiCreate,
  updateCron as apiUpdate,
  deleteCron as apiDelete,
  toggleCron as apiToggle,
  runCronNow as apiRun,
  cronHistory as apiHistory,
} from '@/api/cron'
import type { CronJob, CronRun } from '@/api/cron'
import { toast } from '@/api/http'

export const useCronStore = defineStore('cron', () => {
  const jobs = ref<CronJob[]>([])
  const loading = ref(false)
  const history = ref<CronRun[]>([])

  async function load() {
    loading.value = true
    try {
      jobs.value = await listCron()
    } catch (e: any) {
      toast('error', e?.message || '加载定时任务失败')
    } finally {
      loading.value = false
    }
  }

  async function create(body: Partial<CronJob>) {
    await apiCreate(body)
    toast('success', '已创建定时任务')
    await load()
  }

  async function update(id: number, body: Partial<CronJob>) {
    await apiUpdate(id, body)
    toast('success', '已更新')
    await load()
  }

  async function remove(id: number) {
    await apiDelete(id)
    toast('success', '已删除')
    await load()
  }

  async function toggle(id: number, enabled: boolean) {
    await apiToggle(id, enabled)
    await load()
  }

  async function runNow(id: number): Promise<string> {
    const r = await apiRun(id)
    toast('success', '已触发一次执行')
    return r.output || ''
  }

  async function loadHistory(id: number) {
    history.value = await apiHistory(id)
  }

  return { jobs, loading, history, load, create, update, remove, toggle, runNow, loadHistory }
})
