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
      { path: 'models', name: 'models', component: () => import('@/views/ModelsView.vue'), meta: { title: '模型', icon: 'models' } },
      { path: 'knowledge', name: 'knowledge', component: () => import('@/views/KnowledgeView.vue'), meta: { title: '知识库', icon: 'knowledge' } },
      { path: 'users', name: 'users', component: () => import('@/views/UsersView.vue'), meta: { title: '用户', icon: 'users', role: 'admin' } },
      { path: 'skills', name: 'skills', component: () => import('@/views/SkillsView.vue'), meta: { title: '技能', icon: 'skills', role: 'admin' } },
      { path: 'plugins', name: 'plugins', component: () => import('@/views/PluginsView.vue'), meta: { title: '插件', icon: 'plugins', role: 'admin' } },
      { path: 'mcp', name: 'mcp', component: () => import('@/views/McpView.vue'), meta: { title: 'MCP', icon: 'mcp', role: 'admin' } },
      { path: 'memory', name: 'memory', component: () => import('@/views/MemoryView.vue'), meta: { title: '记忆', icon: 'memory', role: 'admin' } },
      { path: 'agents', name: 'agents', component: () => import('@/views/AgentsView.vue'), meta: { title: '智能体', icon: 'agents', role: 'admin' } },
      { path: 'cron', name: 'cron', component: () => import('@/views/CronView.vue'), meta: { title: '定时任务', icon: 'cron', role: 'admin' } },
      { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '设置', icon: 'settings' } },
      { path: 'system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { title: '系统', icon: 'system', role: 'admin' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({ history: createWebHashHistory(), routes })

// 统一守卫：公共页放行；未登录跳登录；admin 路由校验角色
router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!hasToken()) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.meta.role === 'admin') {
    const u = getUser()
    if (!u || u.role !== 'admin') return { name: 'chat' }
  }
  return true
})

export default router
