<template>
  <n-config-provider
    :theme="isDark ? darkTheme : null"
    :theme-overrides="overrides"
  >
    <n-message-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <router-view v-slot="{ Component }">
            <component :is="Component" />
          </router-view>
        </n-notification-provider>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { darkTheme, NConfigProvider, NMessageProvider, NDialogProvider, NNotificationProvider } from 'naive-ui'
import { getThemeOverrides } from '@/styles/theme'
import { useTheme } from '@/composables/useTheme'
import { hasToken } from '@/api/client'
import { useAppStore } from '@/stores/app'

const { isDark } = useTheme()
const overrides = computed(() => getThemeOverrides(isDark.value))
const app = useAppStore()

onMounted(async () => {
  if (hasToken()) {
    await app.refreshMe()
    await app.loadModels()
  }
})
</script>
