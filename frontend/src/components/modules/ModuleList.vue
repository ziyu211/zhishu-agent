<script setup lang="ts">
import { ref, computed } from 'vue'
import { NInput, NButton, NSwitch, NTag, NEmpty, NPopconfirm } from 'naive-ui'

const props = defineProps<{
  title: string
  items: any[]
  loading?: boolean
  emptyText?: string
  searchPlaceholder?: string
  editable?: boolean
  showStatus?: boolean
  exportable?: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle', payload: { name: string; enabled: boolean }): void
  (e: 'refresh'): void
  (e: 'edit', item: any): void
  (e: 'delete', item: any): void
  (e: 'export', item: any): void
}>()

const keyword = ref('')
const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return props.items
  return props.items.filter(
    (it) =>
      (it.name || '').toLowerCase().includes(k) ||
      (it.description || '').toLowerCase().includes(k),
  )
})

function onToggle(it: any, enabled: boolean) {
  emit('toggle', { name: it.name, enabled })
}
</script>

<template>
  <div class="module-list">
    <div class="panel-head">
      <div>
        <div class="panel-count">{{ items.length }} 个{{ title }}</div>
        <div class="panel-sub">已启用 {{ items.filter((p) => p.enabled !== false).length }} 个</div>
      </div>
      <NButton size="small" :loading="loading" quaternary @click="emit('refresh')">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
          </svg>
        </template>
        刷新
      </NButton>
    </div>

    <NInput
      v-model:value="keyword"
      :placeholder="searchPlaceholder || '搜索...'"
      clearable
      size="small"
      class="search-box"
    >
      <template #prefix>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
      </template>
    </NInput>

    <div v-if="!filtered.length" class="empty-wrap">
      <NEmpty :description="keyword ? '没有匹配的结果' : (emptyText || '暂无数据')" />
    </div>

    <div v-else class="card-grid">
      <div v-for="it in filtered" :key="it.name" class="module-card">
        <div class="card-main">
          <div class="card-title-row">
            <span class="card-name">{{ it.name }}</span>
            <NTag v-if="it.version" size="tiny" :bordered="false" class="ver-tag">{{ it.version }}</NTag>
            <NTag v-if="showStatus && it.connected" size="tiny" type="success" :bordered="false">已连接</NTag>
            <NTag v-if="showStatus && it.connected === false" size="tiny" type="warning" :bordered="false">未连接</NTag>
            <NTag v-if="it.tool_count !== undefined" size="tiny" :bordered="false" class="tool-tag">{{ it.tool_count }} 工具</NTag>
            <NTag v-if="it.shared" size="tiny" type="info" :bordered="false">共享</NTag>
            <NTag v-else-if="it.owner" size="tiny" :bordered="false" class="owner-tag">{{ it.owner }}</NTag>
            <NTag v-else size="tiny" :bordered="false" class="owner-tag">公共</NTag>
          </div>
          <div class="card-desc">{{ it.description || '暂无描述' }}</div>
          <div v-if="it.command" class="card-meta">
            <span class="meta-label">命令</span>
            <code class="meta-code">{{ it.command }}<template v-if="it.args && it.args.length"> {{ it.args.join(' ') }}</template></code>
          </div>
          <div v-if="showStatus && it.error" class="card-error" :title="it.error">连接异常：{{ it.error.slice(0, 60) }}</div>
        </div>
        <div class="card-right">
          <div class="card-actions" v-if="editable || exportable">
            <NButton v-if="exportable" size="tiny" quaternary type="default" @click="emit('export', it)">导出</NButton>
            <NButton v-if="editable && it._editable !== false" size="tiny" quaternary type="primary" @click="emit('edit', it)">编辑</NButton>
            <NPopconfirm v-if="editable && it._editable !== false" @positive-click="emit('delete', it)">
              <template #trigger>
                <NButton size="tiny" quaternary type="error">删除</NButton>
              </template>
              确认删除「{{ it.name }}」？此操作不可恢复。
            </NPopconfirm>
          </div>
          <NSwitch :value="it.enabled" :disabled="it._editable === false" @update:value="(v: boolean) => onToggle(it, v)" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.module-list { padding: 20px; }

.panel-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 12px;
}
.panel-count { font-size: 15px; font-weight: 600; color: $text-primary; }
.panel-sub { font-size: 12px; color: $text-muted; margin-top: 2px; }

.search-box { max-width: 320px; margin-bottom: 16px; }

.empty-wrap { padding: 48px 0; display: flex; justify-content: center; }

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.module-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  transition: border-color $transition-fast;
  &:hover { border-color: $accent-muted; }
}

.card-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-name { font-size: 14px; font-weight: 600; color: $text-primary; }
.ver-tag { font-family: $font-code; }
.tool-tag { font-family: $font-code; color: $text-secondary; }

.card-desc {
  font-size: 12px;
  color: $text-muted;
  margin-top: 6px;
  line-height: 1.5;
  max-height: 3em;
  overflow: hidden;
}
.card-error { font-size: 11px; color: #d03050; margin-top: 6px; }

.card-meta { margin-top: 8px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.meta-label { font-size: 11px; color: $text-muted; flex-shrink: 0; }
.meta-code {
  font-family: $font-code;
  font-size: 11px;
  color: $text-secondary;
  background: $code-bg;
  padding: 2px 6px;
  border-radius: 4px;
  word-break: break-all;
}

.card-right { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; flex-shrink: 0; }
.card-actions { display: flex; gap: 2px; }
</style>
