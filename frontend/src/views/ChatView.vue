<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { NButton, NInput, NModal, NSelect, useMessage } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { useAppStore } from '@/stores/app'
import { api } from '@/api/client'
import MessageList from '@/components/chat/MessageList.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import SessionListItem from '@/components/chat/SessionListItem.vue'
import DocViewer from '@/components/DocViewer.vue'

const chat = useChatStore()
const app = useAppStore()
const message = useMessage()

const showSessions = ref(true)
const showRename = ref(false)
const renameValue = ref('')
const renameId = ref('')
const modelOptions = ref<any[]>([])
// 多 Agent 协作：聊天页智能体选择器（默认主管自动编排）
const agentOptions = ref<any[]>([])
const selectedAgent = computed({
  get: () => chat.selectedAgent,
  set: (v: string) => (chat.selectedAgent = v || ''),
})

const pinnedSessions = computed(() => chat.sorted.filter((s) => s.pinned))
const normalSessions = computed(() => chat.sorted.filter((s) => !s.pinned))

// 前端侧模型类型推断（与后端 classify_model 一致），用于选择器分类标注
function classifyModel(name: string): 'text' | 'image' | 'video' {
  const n = (name || '').toLowerCase()
  if (/video|sora|kling|cogvideo|hunyuanvideo|wan-video|seedance|vidu|hailuo|veo|runway|视频/.test(n)) return 'video'
  if (/image|dall-?e|gpt-image|stable-diffusion|sd3|sdxl|flux|cogview|wanx|kolors|seedream|hunyuan-image|irag|画|绘/.test(n)) return 'image'
  return 'text'
}
const KIND_TAG: Record<string, string> = { text: '文', image: '图', video: '视' }

async function loadModels() {
  try {
    const r = await api.listModels()
    const groups: Record<string, any[]> = { text: [], image: [], video: [] }
    for (const p of r.providers || []) {
      for (const m of p.models || []) {
        const kind = classifyModel(m)
        groups[kind].push({
          label: `[${KIND_TAG[kind]}] ${p.label} / ${m}`,
          value: `${p.provider}/${m}`,
        })
      }
    }
    const opts: any[] = []
    const groupLabel: Record<string, string> = { text: '文本对话', image: '图像生成', video: '视频生成' }
    for (const k of ['text', 'image', 'video']) {
      if (groups[k].length) opts.push({ type: 'group', label: groupLabel[k], key: k, children: groups[k] })
    }
    modelOptions.value = opts
    if (!app.selectedModel && r.default_model) app.selectModel(r.default_model)
  } catch {
    /* 静默 */
  }
}

// 加载已启用子智能体，用于聊天页选择器
async function loadAgents() {
  try {
    const r = await api.agentOptions()
    const list = (r.agents || []).map((a: any) => ({
      label: `子智能体 · ${a.name}${a.description ? '（' + a.description + '）' : ''}`,
      value: a.name,
    }))
    agentOptions.value = [{ label: '主管（自动编排/委派）', value: '' }, ...list]
  } catch {
    agentOptions.value = [{ label: '主管（自动编排/委派）', value: '' }]
  }
}

function newChat() {
  chat.newSession()
  if (window.innerWidth <= 768) showSessions.value = false
}
function handleRename(id: string) {
  const s = chat.sessions.find((x) => x.id === id)
  renameId.value = id
  renameValue.value = s?.title || ''
  showRename.value = true
}
function confirmRename() {
  if (renameId.value && renameValue.value.trim()) {
    chat.renameSession(renameId.value, renameValue.value.trim())
  }
  showRename.value = false
}

onMounted(() => {
  chat.reset()
  chat.loadSessions()
  loadModels()
  loadAgents()
})
</script>

<template>
  <div class="chat-view">
    <div v-if="showSessions" class="session-backdrop" @click="showSessions = false"></div>
    <aside class="session-list" :class="{ collapsed: !showSessions }">
      <div class="session-list-header">
        <span class="session-list-title">对话</span>
        <div class="session-list-actions">
          <NButton quaternary size="tiny" @click="showSessions = false" circle>
            <template #icon><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></template>
          </NButton>
          <NButton quaternary size="tiny" @click="newChat" circle>
            <template #icon><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></template>
          </NButton>
        </div>
      </div>
      <div class="session-items">
        <div v-if="chat.sessions.length === 0" class="session-empty">暂无对话</div>

        <template v-if="pinnedSessions.length">
          <div class="session-group-header"><span class="session-group-label">置顶</span><span class="session-group-count">{{ pinnedSessions.length }}</span></div>
          <SessionListItem
            v-for="s in pinnedSessions"
            :key="s.id"
            :session="s"
            :active="s.id === chat.activeId"
            :pinned="true"
            :show-owner="app.isAdmin"
            @select="chat.switchSession(s.id); if(window.innerWidth<=768) showSessions=false"
            @pin="chat.togglePin(s.id)"
            @rename="handleRename(s.id)"
            @delete="chat.removeSession(s.id)"
          />
        </template>

        <template v-if="normalSessions.length">
          <div class="session-group-header"><span class="session-group-label">最近</span><span class="session-group-count">{{ normalSessions.length }}</span></div>
          <SessionListItem
            v-for="s in normalSessions"
            :key="s.id"
            :session="s"
            :active="s.id === chat.activeId"
            :pinned="false"
            :show-owner="app.isAdmin"
            @select="chat.switchSession(s.id); if(window.innerWidth<=768) showSessions=false"
            @pin="chat.togglePin(s.id)"
            @rename="handleRename(s.id)"
            @delete="chat.removeSession(s.id)"
          />
        </template>
      </div>
    </aside>

    <div class="chat-main">
      <header class="chat-header">
        <div class="header-left">
          <NButton v-if="!showSessions" quaternary size="small" @click="showSessions = true" circle>
            <template #icon><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg></template>
          </NButton>
          <span class="header-session-title">{{ chat.active?.title || '对话' }}</span>
        </div>
        <div class="header-actions">
          <NSelect
            v-model:value="selectedAgent"
            :options="agentOptions"
            placeholder="智能体"
            clearable
            size="small"
            style="width: 220px"
          />
          <NSelect
            v-model:value="app.selectedModel"
            :options="modelOptions"
            placeholder="默认模型"
            clearable
            size="small"
            style="width: 240px"
            @update:value="app.selectModel"
          />
          <NButton size="small" @click="newChat">
            <template #icon><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></template>
            新对话
          </NButton>
        </div>
      </header>

      <MessageList />
      <ChatInput />
      <DocViewer />
    </div>

    <NModal
      v-model:show="showRename"
      preset="dialog"
      title="重命名对话"
      positive-text="确定"
      negative-text="取消"
      @positive-click="confirmRename"
    >
      <NInput v-model:value="renameValue" placeholder="输入新标题" @keydown.enter="confirmRename" />
    </NModal>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.chat-view { height: calc(100 * var(--vh)); display: flex; position: relative; }

.session-list {
  width: 220px;
  border-right: 1px solid $border-color;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width $transition-normal, opacity $transition-normal;
  overflow: hidden;
  &.collapsed { width: 0; border-right: none; opacity: 0; pointer-events: none; }

  @media (max-width: $breakpoint-mobile) {
    position: absolute; left: 0; top: 0; height: 100%; z-index: 10; background: $bg-card;
    box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1); width: 280px;
    &.collapsed { transform: translateX(-100%); opacity: 0; }
  }
}

.session-backdrop { display: none; @media (max-width: $breakpoint-mobile) {
  display: block; position: absolute; inset: 0; background: rgba(0,0,0,0.4); z-index: 9; } }

.session-list-header { display: flex; align-items: center; justify-content: space-between; padding: 12px; flex-shrink: 0; }
.session-list-actions { display: flex; align-items: center; gap: 4px; }
.session-list-title { font-size: 12px; font-weight: 600; color: $text-muted; text-transform: uppercase; letter-spacing: 0.5px; }
.session-items { flex: 1; overflow-y: auto; padding: 0 6px 12px; }
.session-empty { padding: 16px 10px; font-size: 12px; color: $text-muted; text-align: center; }
.session-group-header { display: flex; align-items: center; gap: 4px; padding: 6px 10px 4px; }
.session-group-label { font-size: 10px; font-weight: 600; color: $text-muted; text-transform: uppercase; letter-spacing: 0.5px; }
.session-group-count { font-size: 10px; color: $text-muted; }

.chat-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

.chat-header { display: flex; align-items: center; justify-content: space-between; padding: 21px 20px; border-bottom: 1px solid $border-color; flex-shrink: 0; }
.header-left { display: flex; align-items: center; gap: 8px; overflow: hidden; flex: 1; min-width: 0; }
.header-session-title { font-size: 16px; font-weight: 600; color: $text-primary; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.header-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

@media (max-width: $breakpoint-mobile) {
  .chat-header { padding: 16px 12px 16px 52px; }
}
</style>
