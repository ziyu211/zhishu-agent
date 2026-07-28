<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useChatStore } from '@/stores/chat'
import MessageItem from './MessageItem.vue'

const chat = useChatStore()
const box = ref<HTMLElement | null>(null)

function scrollToBottom() {
  nextTick(() => {
    if (box.value) box.value.scrollTop = box.value.scrollHeight
  })
}
watch(
  () => chat.active?.messages.length,
  scrollToBottom,
)
watch(
  () => chat.active?.messages.map((m) => m.content + (m.toolResult || '')).join('|'),
  scrollToBottom,
)

/** 滚动到指定消息并短暂高亮（供思维图谱「点击概念跳转」使用）。 */
function scrollToMessage(id: string) {
  nextTick(() => {
    const boxEl = box.value
    if (!boxEl) return
    const el = Array.from(boxEl.querySelectorAll<HTMLElement>('[data-mid]')).find(
      (e) => e.dataset.mid === id,
    )
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('msg-flash')
    window.setTimeout(() => el.classList.remove('msg-flash'), 1600)
  })
}

defineExpose({ scrollToMessage })
</script>

<template>
  <div ref="box" class="message-list">
    <div v-if="!chat.active || chat.active.messages.length === 0" class="empty">
      <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
      <p>开始与智枢对话吧</p>
      <span class="empty-sub">支持工具调用与知识库检索</span>
    </div>

    <template v-else>
      <div
        v-for="m in chat.active.messages"
        :key="m.id"
        class="msg-row"
        :data-mid="m.id"
      >
        <MessageItem :message="m" />
      </div>
    </template>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.empty {
  margin: auto;
  text-align: center;
  color: $text-muted;
  svg { margin-bottom: 8px; opacity: 0.6; }
  p { font-size: 14px; margin: 0; }
  .empty-sub { font-size: 12px; }
}

/* 思维图谱「点击概念跳转」时短暂高亮目标消息 */
.msg-row.msg-flash {
  animation: msgFlash 1.6s ease-out;
  border-radius: 12px;
}
@keyframes msgFlash {
  0% { box-shadow: 0 0 0 2px $accent-primary; }
  55% { box-shadow: 0 0 0 2px rgba(120, 150, 255, 0.45); }
  100% { box-shadow: 0 0 0 0 transparent; }
}
</style>
