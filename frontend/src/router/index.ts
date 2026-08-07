import { createRouter, createWebHashHistory } from 'vue-router'
import { hasToken, getUser } from '@/api/client'
import MainLayout from '@/components/layout/MainLayout.vue'
import LoginView from '@/views/LoginView.vue'

// 中央路由表（对标 hermes-web-ui：全部 lazy import + 统一 beforeEach 守卫）
const routes = [
  { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
  {
    path: '/',
    component: MainLayout,
    redirect: '/chat',
    children: [
      { path: 'chat', name: 'chat', component: () => import('@/views/ChatView.vue'), meta: { title: '对话', icon: 'chat' } },
      { path: 'models', name: 'models', component: () => import('@/views/ModelsView.vue'), meta: { title: '模型', icon: 'models', perm: 'models:read' } },
      { path: 'knowledge', name: 'knowledge', component: () => import('@/views/KnowledgeView.vue'), meta: { title: '知识库', icon: 'knowledge', perm: 'knowledge:read' } },
      { path: 'users', name: 'users', component: () => import('@/views/UsersView.vue'), meta: { title: '用户', icon: 'users', perm: 'users:read' } },
      { path: 'skills', name: 'skills', component: () => import('@/views/SkillsView.vue'), meta: { title: '技能', icon: 'skills', perm: 'modules:read' } },
      { path: 'plugins', name: 'plugins', component: () => import('@/views/PluginsView.vue'), meta: { title: '插件', icon: 'plugins', perm: 'modules:read' } },
      { path: 'mcp', name: 'mcp', component: () => import('@/views/McpView.vue'), meta: { title: 'MCP', icon: 'mcp', perm: 'modules:read' } },
      { path: 'memory', name: 'memory', component: () => import('@/views/MemoryView.vue'), meta: { title: '记忆', icon: 'memory', perm: 'modules:read' } },
      { path: 'agents', name: 'agents', component: () => import('@/views/AgentsView.vue'), meta: { title: '智能体', icon: 'agents', perm: 'agents:read' } },
      { path: 'cron', name: 'cron', component: () => import('@/views/CronView.vue'), meta: { title: '定时任务', icon: 'cron', perm: 'cron:read' } },
      { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '设置', icon: 'settings', perm: 'admin' } },
      { path: 'system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { title: '系统', icon: 'system', perm: 'system:read' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({ history: createWebHashHistory(), routes })

/** 基于登录用户权限集合判定是否拥有某权限（与后端 AuthService.can 同源） */
function canPerm(perm: string): boolean {
  const u = getUser() as any
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
}

// 统一守卫：公共页放行；未登录跳登录；受限路由按权限判定可达性
router.beforeEach((to) => {
  if ((to.meta as any).public) return true
  if (!hasToken()) return { name: 'login', query: { redirect: to.fullPath } }
  const perm = (to.meta as any).perm as string | undefined
  if (perm && !canPerm(perm)) return { name: 'chat' }
  return true
})

export default router
