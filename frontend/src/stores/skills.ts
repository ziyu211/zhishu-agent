/**
 * 技能 Skills store。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listSkills,
  getSkill,
  createSkill as apiCreate,
  updateSkill as apiUpdate,
  deleteSkill as apiDelete,
  toggleSkill as apiToggle,
} from '@/api/skills'
import { toast } from '@/api/http'
import type { SkillItem } from '@/api/types'

export const useSkillsStore = defineStore('skills', () => {
  const skills = ref<SkillItem[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      skills.value = await listSkills()
    } catch (e: any) {
      toast('error', e?.message || '加载技能失败')
    } finally {
      loading.value = false
    }
  }
  async function get(name: string) {
    return getSkill(name)
  }
  async function create(body: any) {
    await apiCreate(body)
    toast('success', '已创建技能')
    await load()
  }
  async function update(name: string, body: any) {
    await apiUpdate(name, body)
    toast('success', '已更新技能')
    await load()
  }
  async function remove(name: string) {
    await apiDelete(name)
    toast('success', '已删除技能')
    await load()
  }
  async function toggle(name: string, enabled: boolean) {
    await apiToggle(name, enabled)
    await load()
  }

  return { skills, loading, load, get, create, update, remove, toggle }
})
