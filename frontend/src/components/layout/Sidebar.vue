<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useTheme } from '@/composables/useTheme'
import { getToken } from '@/api/client'
import { useAppStore } from '@/stores/app'
import ThemeSwitch from './ThemeSwitch.vue'
import UserMenu from './UserMenu.vue'
import ModelSelector from './ModelSelector.vue'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()
const { isDark } = useTheme()
const app = useAppStore()

const token = computed(() => getToken())
const selectedKey = computed(() => route.name as string)

// 菜单可见性由角色权限驱动（与后端 RBAC 同源）：未配置 perm 的项对所有登录用户可见
const PERM: Record<string, string> = {
  chat: 'chat',
  knowledge: 'knowledge:read',
  models: 'models:read',
  skills: 'modules:read',
  plugins: 'modules:read',
  mcp: 'modules:read',
  memory: 'modules:read',
  agents: 'agents:read',
  cron: 'cron:read',
  users: 'users:read',
  system: 'system:read',
  // settings：无权限要求，所有登录用户可见
}
const navGroups = computed(() =>
  groups.map((g) => ({
    ...g,
    items: g.items.filter((it) => !PERM[it.key] || app.can(PERM[it.key])),
  })),
)

// 分组导航（参照 hermes-web-ui「黑白水墨」侧边栏分组 + 内联 SVG 描边图标）
// 文案走 i18n（locales/zh.ts），后续可扩展其他语言。
const groups = [
  {
    i18nKey: 'groups.agent',
    items: [
      { key: 'chat', i18nKey: 'nav.chat', icon: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' },
      { key: 'knowledge', i18nKey: 'nav.knowledge', icon: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z' },
    ],
  },
  {
    i18nKey: 'groups.admin',
    items: [
      { key: 'models', i18nKey: 'nav.models', icon: 'M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1' },
      { key: 'skills', i18nKey: 'nav.skills', icon: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5' },
      { key: 'plugins', i18nKey: 'nav.plugins', icon: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z' },
      { key: 'mcp', i18nKey: 'nav.mcp', icon: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z' },
      { key: 'memory', i18nKey: 'nav.memory', icon: 'M12 2C7 2 3 3.5 3 5.5S7 9 12 9s9-1.5 9-3.5S17 2 12 2zM3 5.5V12c0 2 4 3.5 9 3.5s9-1.5 9-3.5V5.5M3 12v6.5C3 20.5 7 22 12 22s9-1.5 9-3.5V12' },
      { key: 'agents', i18nKey: 'nav.agents', icon: 'M12 2a5 5 0 0 1 5 5v2a5 5 0 0 1-2 4v2a3 3 0 0 1-6 0v-2a5 5 0 0 1-2-4V7a5 5 0 0 1 5-5zM12 22v-3' },
      { key: 'cron', i18nKey: 'nav.cron', icon: 'M12 8v4l3 3M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z' },
      { key: 'users', i18nKey: 'nav.users', icon: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z' },
      { key: 'system', i18nKey: 'nav.system', icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z' },
    ],
  },
  {
    i18nKey: 'groups.preference',
    items: [
      { key: 'settings', i18nKey: 'nav.settings', icon: 'M3 6h18M3 12h18M3 18h18' },
    ],
  },
]

const collapsedGroups = reactive<Record<string, boolean>>({})
function toggleGroup(key: string) {
  collapsedGroups[key] = !collapsedGroups[key]
}
function isCollapsed(key: string) {
  return !!collapsedGroups[key]
}
function handleNav(key: string) {
  router.push('/' + key)
}
</script>

<template>
  <aside class="sidebar">
    <!-- Logo：中国红方块 + 智枢（品牌色仅此处使用） -->
    <div class="sidebar-logo" @click="handleNav('chat')">
      <span class="logo-mark">智</span>
      <span class="logo-text">{{ t('app.name') }}</span>
      <span class="logo-sub">{{ t('app.tagline') }}</span>
    </div>

    <nav class="sidebar-nav">
      <div v-for="g in navGroups" :key="g.i18nKey" class="nav-group">
        <div class="nav-group-label" @click="toggleGroup(g.i18nKey)">
          <span>{{ t(g.i18nKey) }}</span>
          <svg class="nav-group-arrow" :class="{ collapsed: isCollapsed(g.i18nKey) }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
        </div>
        <div v-show="!isCollapsed(g.i18nKey)">
          <button
            v-for="it in g.items"
            :key="it.key"
            class="nav-item"
            :class="{ active: selectedKey === it.key }"
            @click="handleNav(it.key)"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path :d="it.icon" /></svg>
            <span>{{ t(it.i18nKey) }}</span>
          </button>
        </div>
      </div>
    </nav>

    <div class="sidebar-footer">
      <ModelSelector v-if="token" />
      <div class="status-row">
        <div class="status-indicator" :class="token ? 'connected' : 'disconnected'">
          <span class="status-dot"></span>
          <span class="status-text">{{ token ? t('common.connected') : t('common.disconnected') }}</span>
        </div>
        <ThemeSwitch v-if="token" />
      </div>
      <UserMenu v-if="token" />
    </div>
  </aside>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.sidebar {
  width: $sidebar-width;
  height: calc(100 * var(--vh));
  background-color: $bg-sidebar;
  border-right: 1px solid $border-color;
  display: flex;
  flex-direction: column;
  padding: 0 12px 20px;
  flex-shrink: 0;
  transition: width $transition-normal;
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 12px;
  margin: 0 -12px 8px;
  background-color: $bg-card;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  cursor: pointer;

  .dark & { background-color: #2f2f2f; }

  .logo-mark {
    width: 30px;
    height: 30px;
    flex-shrink: 0;
    border-radius: 7px;
    background: var(--brand);
    color: #fff;
    font-weight: 700;
    font-size: 17px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: $font-ui;
  }
  .logo-text {
    font-size: 16px;
    font-weight: 600;
    color: $text-primary;
    letter-spacing: 0.5px;
    line-height: 1;
  }
  .logo-sub {
    font-size: 11px;
    color: $text-muted;
    align-self: flex-end;
    padding-bottom: 1px;
  }
}

.sidebar-nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 8px;
  overflow-y: auto;
  min-height: 0;
  scrollbar-width: none;
  &::-webkit-scrollbar { display: none; }
}

.nav-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-group-label {
  font-size: 10px;
  font-weight: 600;
  color: $text-muted;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  padding: 8px 12px 4px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  border-radius: $radius-sm;
  transition: color $transition-fast;

  &:hover { color: $text-secondary; }
}

.nav-group-arrow {
  transition: transform $transition-fast;
  flex-shrink: 0;
  &.collapsed { transform: rotate(-90deg); }
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border: none;
  background: none;
  color: $text-secondary;
  font-size: 14px;
  border-radius: $radius-sm;
  cursor: pointer;
  transition: all $transition-fast;
  width: 100%;
  text-align: left;

  &:hover {
    background-color: rgba(var(--accent-primary-rgb), 0.06);
    color: $text-primary;
  }
  &.active {
    background-color: rgba(var(--accent-primary-rgb), 0.12);
    color: $accent-primary;
  }
}

.sidebar-footer {
  padding-top: 8px;
  border-top: 1px solid $border-color;
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  &.connected .status-dot {
    background-color: $success;
    box-shadow: 0 0 6px rgba(var(--success-rgb), 0.5);
  }
  &.disconnected .status-dot { background-color: $error; }
  .status-text { color: $text-secondary; }
}
</style>
