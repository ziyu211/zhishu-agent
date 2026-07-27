/**
 * 定时任务相关 API。
 */
import { request } from './http'

export interface CronJob {
  id: number
  name: string
  schedule_type: 'interval' | 'daily' | 'cron'
  schedule_config: Record<string, any>
  action: 'chat' | 'shell'
  payload: string
  model?: string | null
  owner?: string | null
  enabled: boolean
  last_run?: string | null
  next_run?: string | null
  created_at?: string
}

export interface CronRun {
  id: number
  job_id: number
  started_at: string
  finished_at?: string
  status: string
  output?: string
}

export const listCron = () => request<CronJob[]>('/api/v1/cron')
export const createCron = (body: Partial<CronJob>) =>
  request<any>('/api/v1/cron', { method: 'POST', body })
export const updateCron = (id: number, body: Partial<CronJob>) =>
  request<any>(`/api/v1/cron/${id}`, { method: 'PUT', body })
export const deleteCron = (id: number) =>
  request<any>(`/api/v1/cron/${id}`, { method: 'DELETE' })
export const toggleCron = (id: number, enabled: boolean) =>
  request<any>(`/api/v1/cron/${id}/toggle?enabled=${enabled}`, { method: 'PUT' })
export const runCronNow = (id: number) =>
  request<{ ok: boolean; output: string }>(`/api/v1/cron/${id}/run`, { method: 'POST' })
export const cronHistory = (id: number, limit = 20) =>
  request<CronRun[]>(`/api/v1/cron/${id}/history?limit=${limit}`)

export const cronApi = {
  listCron,
  createCron,
  updateCron,
  deleteCron,
  toggleCron,
  runCronNow,
  cronHistory,
}
