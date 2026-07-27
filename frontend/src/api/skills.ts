/**
 * 技能 Skills API。
 */
import { request } from './http'
import type { SkillItem } from './types'

export const listSkills = () => request<SkillItem[]>('/api/v1/skills')
export const getSkill = (name: string) =>
  request<SkillItem>(`/api/v1/skills/${encodeURIComponent(name)}`)
export const createSkill = (body: any) => request<any>('/api/v1/skills', { method: 'POST', body })
export const updateSkill = (name: string, body: any) =>
  request<any>(`/api/v1/skills/${encodeURIComponent(name)}`, { method: 'PUT', body })
export const deleteSkill = (name: string) =>
  request<any>(`/api/v1/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
export const toggleSkill = (name: string, enabled: boolean) =>
  request<any>(`/api/v1/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'PUT',
    body: { enabled },
  })

export const skillsApi = {
  listSkills,
  getSkill,
  createSkill,
  updateSkill,
  deleteSkill,
  toggleSkill,
}
