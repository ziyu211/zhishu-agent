import { defineStore } from 'pinia'
import { api, getUser, saveUser, clearToken, getToken } from '@/api/client'

const MODEL_KEY = 'zhishu.selectedModel'

export const useAppStore = defineStore('app', {
  state: () => ({
    user: getUser(),
    models: [] as { provider: string; label: string; models: string[]; local: boolean; base_url: string }[],
    defaultModel: '' as string,
    selectedModel: (typeof localStorage !== 'undefined' ? localStorage.getItem(MODEL_KEY) : '') || '',
    connected: false,
    serverVersion: '1.0.0',
    loadingModels: false,
  }),
  getters: {
    isAdmin: (s) => s.user?.role === 'admin',
    /** 当前用户权限集合（来自后端 ROLES，admin 为 ['*']） */
    perms: (s) => (s.user as any)?.perms || [],
    /** 扁平化模型选项：provider/model */
    modelOptions: (s) => {
      const opt: { value: string; label: string; provider: string }[] = []
      for (const p of s.models) {
        for (const m of p.models) {
          opt.push({ value: `${p.provider}/${m}`, label: `${m}`, provider: p.provider })
        }
      }
      return opt
    },
    /** 权限判断：admin('*') 恒真；拥有写权限隐含读权限 */
    can: (s) => (perm: string): boolean => {
      const u = s.user as any
      if (!u) return false
      if (u.role === 'admin') return true
      const p: string[] = u.perms || []
      if (p.includes('*')) return true
      if (p.includes(perm)) return true
      if (perm.endsWith(':read')) {
        const w = perm.replace(':read', ':write')
        if (p.includes(w)) return true
      }
      return false
    },
  },
  actions: {
    setUser(u: any) {
      this.user = u
      if (u) saveUser(u)
    },
    logout() {
      clearToken()
      this.user = null
      this.models = []
    },
    async loadModels() {
      this.loadingModels = true
      try {
        const r = await api.listModels()
        this.models = r.providers || []
        this.defaultModel = r.default_model || ''
        // 校验已恢复的选择是否仍可用；失效则回退默认
        const valid = this.modelOptions.some((o) => o.value === this.selectedModel)
        if (!this.selectedModel || !valid) {
          this.selectedModel = this.defaultModel
          try {
            if (this.defaultModel) localStorage.setItem(MODEL_KEY, this.defaultModel)
          } catch {
            /* noop */
          }
        }
      } catch (e) {
        // 忽略，保持现状
      } finally {
        this.loadingModels = false
      }
    },
    selectModel(m: string) {
      this.selectedModel = m
      try {
        if (m) localStorage.setItem(MODEL_KEY, m)
        else localStorage.removeItem(MODEL_KEY)
      } catch {
        /* 忽略存储异常 */
      }
    },
    async refreshMe() {
      try {
        const me = await api.me()
        this.setUser(me)
      } catch {
        /* noop */
      }
    },
    setConnected(v: boolean) {
      this.connected = v
    },
  },
})
