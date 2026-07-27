<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'

const app = useAppStore()
const { t } = useI18n()
const router = useRouter()

const user = computed(() => app.user)
const display = computed(() => user.value?.display_name || user.value?.user || '?')
const initial = computed(() => (display.value[0] || '?').toUpperCase())

function doLogout() {
  app.logout()
  router.replace('/login')
}
</script>

<template>
  <div class="user-menu">
    <div class="user-info">
      <div class="avatar">{{ initial }}</div>
      <div class="meta">
        <div class="name">{{ display }}</div>
        <div class="role">{{ user?.role_label || user?.role }}</div>
      </div>
    </div>
    <NButton block size="small" quaternary class="logout-item" @click="doLogout">
      {{ t('common.logout') }}
    </NButton>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.user-menu {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;

  .avatar {
    width: 30px;
    height: 30px;
    border-radius: 50%;
    background: rgba(var(--accent-primary-rgb), 0.12);
    color: $accent-primary;
    font-weight: 600;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .meta {
    min-width: 0;
    .name {
      font-size: 13px;
      font-weight: 600;
      color: $text-primary;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .role {
      font-size: 11px;
      color: $text-muted;
    }
  }
}

.logout-item {
  color: $text-muted;
  &:hover {
    color: $error !important;
  }
}
</style>
