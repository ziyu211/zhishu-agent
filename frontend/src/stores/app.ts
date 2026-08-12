import { defineStore } from 'pinia'
import { api, getUser, saveUser, clearToken, getToken } from '@/api/client'
import { clearActAs } from '@/api/actas'

const MODEL_KEY = 'zhishu.selectedModel'

export const useAppStore = defineStore('app', {
  state: () => ({
    user: getUser(),
    models: [] as { provider: string; label: string; models: string[]; local: boolean; base_url: string; has_key?: boolean }[],
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
    /** 扁平化模型选项：provider/model。
     * 兜底：已选模型若不在加载结果中（如后端未启用/加载失败），仍保留为可选项，
     * 避免下拉框被禁用导致普通用户无法切换模型。 */
    modelOptions: (s) => {
      const opt: { value: string; label: string; provider: string }[] = []
      for (const p of s.models) {
        // 隐藏无 API Key 且非本地的云端 Provider：这类 Provider 在 LLM 调用链里会被跳过，
        // 列出来只会让用户选到后「无响应」或报错。
        if (!p.local && !p.has_key) continue
        for (const m of p.models) {
          opt.push({ value: `${p.provider}/${m}`, label: `${m}`, provider: p.provider })
        }
      }
      if (s.selectedModel && !opt.some((o) => o.value === s.selectedModel)) {
        const [provider, ...rest] = s.selectedModel.split('/')
        opt.unshift({ value: s.selectedModel, label: rest.join('/'), provider })
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
      // 归一化登录身份字段：后端 /auth/login、/auth/me 返回的用户名落在 `user`，
      // 而用户列表接口返回 `username`。这里双向补齐，避免视图里取错字段导致
      // 「按 owner 判定可编辑」失效（详见 Skills/Plugins/Mcp/Agents 视图的 markEditable）。
      if (u) {
        if (!u.username && u.user) u.username = u.user
        if (!u.user && u.username) u.user = u.username
      }
      this.user = u
      if (u) saveUser(u)
    },
    async logout() {
      // 先通知后端吊销令牌并清除 /media Cookie，再清本地会话。
      // 即使后端调用失败（如 token 已过期）也保证本地登出，避免卡死。
      try {
        await api.logout()
      } catch {
        /* 后端失败仍继续本地登出 */
      } finally {
        clearToken()
        clearActAs()
        this.user = null
        this.models = []
      }
    },
    async loadModels() {
      this.loadingModels = true
      try {
        const r = await api.listModels()
        this.models = r.providers || []
        this.defaultModel = r.default_model || ''
        // 校验已恢复的选择是否仍可用；失效则回退默认，默认也失效则回退第一个可用模型
        let valid = this.modelOptions.some((o) => o.value === this.selectedModel)
        if (!this.selectedModel || !valid) {
          valid = this.modelOptions.some((o) => o.value === this.defaultModel)
          this.selectedModel = valid ? this.defaultModel : (this.modelOptions[0]?.value || '')
          try {
            if (this.selectedModel) localStorage.setItem(MODEL_KEY, this.selectedModel)
          } catch {
            /* noop */
          }
        }
      } catch (e) {
        // 保持现状，但输出日志便于排查模型列表为空导致选择器被禁用的问题
        console.error('[app] loadModels failed:', e)
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
