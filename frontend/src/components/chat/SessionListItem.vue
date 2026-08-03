<script setup lang="ts">
import { computed } from 'vue'
import { NDropdown } from 'naive-ui'
import type { Session } from '@/stores/chat'

const props = defineProps<{
  session: Session
  active: boolean
  pinned: boolean
  showOwner?: boolean
}>()
const emit = defineEmits<{
  (e: 'select'): void
  (e: 'pin'): void
  (e: 'rename'): void
  (e: 'delete'): void
}>()

const lastText = computed(() => {
  const msgs = props.session.messages
  if (!msgs.length) return '暂无消息'
  const last = msgs[msgs.length - 1]
  return (last.role === 'user' ? '' : '') + (last.content || '(工具调用)').slice(0, 40)
})

const isRunning = computed(() => {
  return props.session.messages.some(
    (m) => m.role === 'assistant' && m.isStreaming === true
  )
})

const menuOptions = computed(() => [
  { label: props.pinned ? '取消置顶' : '置顶', key: 'pin' },
  { label: '重命名', key: 'rename' },
  { label: '删除', key: 'delete' },
])

function onSelect(key: string) {
  if (key === 'pin') emit('pin')
  else if (key === 'rename') emit('rename')
  else if (key === 'delete') emit('delete')
}
</script>

<template>
  <div class="session-item" :class="{ active, pinned }" @click="emit('select')">
    <div class="session-item-content">
      <div class="session-item-title-row">
        <svg v-if="pinned" width="11" height="11" viewBox="0 0 24 24" fill="currentColor" class="session-pin-icon"><path d="M16 3l5 5-4 1-3 3-3-3-4 4-1-1 4-4-3-3 3-3 4 4 3-3z" transform="rotate(45 12 12)"/></svg>
        <span class="session-item-title">{{ session.title }}</span>
        <svg v-if="isRunning" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" class="session-running-icon">
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
        </svg>
      </div>
      <div class="session-item-time">{{ lastText }}</div>
      <div v-if="showOwner && session.owner" class="session-item-owner">@{{ session.owner }}</div>
    </div>
    <NDropdown :options="menuOptions" @select="onSelect" placement="bottom-end" trigger="click">
      <button class="session-item-delete" @click.stop title="更多">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>
      </button>
    </NDropdown>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: none;
  border-radius: $radius-sm;
  cursor: pointer;
  text-align: left;
  color: $text-secondary;
  transition: all $transition-fast;
  margin-bottom: 2px;

  &:hover { background: rgba(var(--accent-primary-rgb), 0.06); color: $text-primary;
    .session-item-delete { opacity: 0.6; } }
  &.active { background: rgba(var(--accent-primary-rgb), 0.12); color: $text-primary; font-weight: 500;
    .session-item-title { color: $accent-primary; } }
}

.session-item-content { flex: 1; overflow: hidden; min-width: 0; }
.session-item-title-row { display: flex; align-items: center; gap: 5px; min-width: 0; }
.session-pin-icon { color: $accent-primary; flex-shrink: 0; }
.session-item-title { display: block; flex: 1 1 auto; min-width: 0; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-item-time { font-size: 11px; color: $text-muted; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.session-item-owner { font-size: 10px; color: $accent-primary; opacity: 0.65; margin-top: 2px; }

.session-item-delete {
  flex-shrink: 0;
  opacity: 0;
  padding: 2px;
  border: none;
  background: none;
  color: $text-muted;
  cursor: pointer;
  border-radius: 3px;
  transition: all $transition-fast;
  &:hover { color: $error; background: rgba(var(--error-rgb), 0.1); }
}

.session-running-icon {
  flex-shrink: 0;
  color: $accent-primary;
  animation: session-spin 1s linear infinite;
}

@keyframes session-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
