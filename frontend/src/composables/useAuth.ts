/**
 * 鉴权 composable（对标 hermes-web-ui 的 useAuth 思路）。
 * 封装 token/user 的登录、刷新、登出，作为视图层的统一鉴权入口。
 */
import { ref } from 'vue'
import {
  setToken,
  saveUser,
  clearToken,
  getToken,
  getUser,
  type LocalUser,
} from '@/api/http'
import { login as apiLogin, me } from '@/api/auth'

export function useAuth() {
  const user = ref<LocalUser | null>(getUser())
  const isAuthed = ref(!!getToken())

  async function login(username: string, password: string): Promise<LocalUser> {
    const r = await apiLogin(username, password)
    // 后端登录返回字段为 token（见后端 auth.py），不是 access_token
    setToken(r.token)
    const u: LocalUser = {
      user: r.user || username,
      role: r.role || 'user',
      role_label: (r as any).role_label,
      display_name: (r as any).display_name,
    }
    saveUser(u)
    user.value = u
    isAuthed.value = true
    return u
  }

  async function refreshMe() {
    try {
      const u = await me()
      saveUser(u as LocalUser)
      user.value = u as LocalUser
      isAuthed.value = true
    } catch {
      /* 保持现状 */
    }
  }

  function logout() {
    clearToken()
    user.value = null
    isAuthed.value = false
  }

  return { user, isAuthed, login, refreshMe, logout }
}
