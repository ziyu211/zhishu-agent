/**
 * 技能 Skills API。
 */
import { request, downloadFile } from './http'
import type { SkillItem, SkillsResp } from './types'

export const listSkills = () => request<SkillsResp>('/api/v1/skills')
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

/**
 * 从外部智能体（Hermes / OpenClaw / 通用压缩包）导入技能。
 * file: 用户选择的 .zip / .tgz 文件。
 */
export const importSkills = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request<any>('/api/v1/skills/import', { method: 'POST', formData: fd })
}

/** 导出全部技能（zip，智枢原生 / Hermes 兼容格式）。 */
export const exportSkills = () => downloadFile('/api/v1/skills/export', 'zhishu-skills.zip')

/** 导出单个技能为 zip。 */
export const exportSkill = (name: string) =>
  downloadFile(`/api/v1/skills/${encodeURIComponent(name)}/export`, `zhishu-skill-${name}.zip`)

export const skillsApi = {
  listSkills,
  getSkill,
  createSkill,
  updateSkill,
  deleteSkill,
  toggleSkill,
  importSkills,
  exportSkills,
  exportSkill,
}
