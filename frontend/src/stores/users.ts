/**
 * 用户管理 store（管理员）。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listUsers,
  listRoles,
  createUser as apiCreate,
  updateUser as apiUpdate,
  resetUserPassword as apiResetPwd,
  deleteUser as apiDelete,
} from '@/api/users'
import { toast } from '@/api/http'
import type { UserItem, RoleItem } from '@/api/types'

export const useUsersStore = defineStore('users', () => {
  const users = ref<UserItem[]>([])
  const roles = ref<RoleItem[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      users.value = (await listUsers()).users || []
    } catch (e: any) {
      toast('error', e?.message || '加载用户失败')
    } finally {
      loading.value = false
    }
  }
  async function loadRoles() {
    try {
      roles.value = (await listRoles()).roles || []
    } catch {
      /* noop */
    }
  }
  async function create(body: any) {
    await apiCreate(body)
    toast('success', '已创建用户')
    await load()
  }
  async function update(uid: number, body: any) {
    await apiUpdate(uid, body)
    toast('success', '已更新用户')
    await load()
  }
  async function resetPassword(uid: number, password: string) {
    await apiResetPwd(uid, password)
    toast('success', '密码已重置')
  }
  async function remove(uid: number) {
    await apiDelete(uid)
    toast('success', '已删除用户')
    await load()
  }

  return { users, roles, loading, load, loadRoles, create, update, resetPassword, remove }
})
