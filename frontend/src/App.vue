<template>
  <n-config-provider
    :theme="isDark ? darkTheme : null"
    :theme-overrides="overrides"
  >
    <n-message-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <router-view v-slot="{ Component }">
            <component :is="Component" :key="viewKey" />
          </router-view>
        </n-notification-provider>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { darkTheme, NConfigProvider, NMessageProvider, NDialogProvider, NNotificationProvider } from 'naive-ui'
import { getThemeOverrides } from '@/styles/theme'
import { useTheme } from '@/composables/useTheme'
import { hasToken } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { actAs } from '@/api/actas'

const { isDark } = useTheme()
const overrides = computed(() => getThemeOverrides(isDark.value))
const app = useAppStore()
const route = useRoute()

// 切换用户（X-Act-As 代管）时强制重建当前视图：各视图 onMounted 会按新身份重新
// 加载数据（智能体下拉、模型、会话、知识库等），避免界面停留在旧身份数据
// （典型表现：admin 切到普通用户后聊天页「智能体」下拉仍残留 admin 私有智能体）。
const viewKey = computed(() => `${route.fullPath}::${actAs.value}`)

onMounted(async () => {
  if (hasToken()) {
    await app.refreshMe()
    await app.loadModels()
  }
})

// 管理员「切换用户」(X-Act-As) 后，侧边栏模型选择器需按被代管身份重新拉取可见 Provider，
// 否则会与聊天页（onMounted 已重载）出现不一致的模型列表，可能选到被代管用户无权使用的模型。
watch(
  () => actAs.value,
  () => {
    if (hasToken()) app.loadModels()
  },
)
</script>
