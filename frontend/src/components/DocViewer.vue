<script setup lang="ts">
import { computed } from 'vue'
import { NModal, NButton, NSkeleton, NScrollbar } from 'naive-ui'
import { useDocViewer } from '@/composables/useDocViewer'

const { state, close } = useDocViewer()

function fmtSize(n: number | undefined): string {
  if (!n) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}
function fmtTime(t: number | undefined): string {
  if (!t) return '—'
  const d = new Date(t * 1000)
  return d.toLocaleString('zh-CN', { hour12: false })
}

const fileType = computed(() => (state.doc?.file_type || 'FILE').toUpperCase())
const metaRows = computed(() => {
  const d = state.doc
  if (!d) return []
  return [
    { label: '类型', value: fileType.value },
    { label: '大小', value: fmtSize(d.size_bytes) },
    { label: '归属', value: d.owner || '共享' },
    { label: '分块', value: `${d.chunk_count ?? 0} 段` },
    { label: '字符数', value: `${(d.char_count ?? 0).toLocaleString()} 字` },
    { label: '上传时间', value: fmtTime(d.created_at) },
  ]
})
const hasContent = computed(() => !!state.doc?.content && String(state.doc.content).trim().length > 0)
</script>

<template>
  <NModal
    :show="state.open"
    preset="card"
    title="文档查看"
    style="width: min(820px, 94vw); max-height: 88vh"
    :bordered="false"
    @close="close"
    @mask-click="close"
  >
    <template #header>
      <div class="dv-header">
        <span class="dv-file-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
        </span>
        <span class="dv-title" :title="state.title">{{ state.title }}</span>
        <span v-if="fileType" class="dv-type-badge">{{ fileType }}</span>
      </div>
    </template>

    <div class="dv-body">
      <!-- 加载中 -->
      <div v-if="state.loading" class="dv-loading">
        <NSkeleton text :repeat="3" />
        <NSkeleton text width="60%" />
      </div>

      <!-- 错误 -->
      <div v-else-if="state.error" class="dv-error">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <span>{{ state.error }}</span>
      </div>

      <!-- 内容 -->
      <template v-else-if="state.doc">
        <div class="dv-meta">
          <div v-for="row in metaRows" :key="row.label" class="dv-meta-item">
            <span class="dv-meta-label">{{ row.label }}</span>
            <span class="dv-meta-value">{{ row.value }}</span>
          </div>
        </div>
        <div class="dv-divider"></div>
        <div class="dv-content-label">提取正文</div>
        <NScrollbar style="max-height: 46vh">
          <pre v-if="hasContent" class="dv-content">{{ state.doc.content }}</pre>
          <div v-else class="dv-empty">该文档无可见正文（如图片等非文本文件，内容已入库用于检索）。</div>
        </NScrollbar>
      </template>
    </div>

    <template #footer>
      <div class="dv-footer">
        <NButton size="small" @click="close">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.dv-header { display: flex; align-items: center; gap: 8px; min-width: 0; }
.dv-file-icon { color: $accent-primary; flex-shrink: 0; display: flex; }
.dv-title { font-size: 15px; font-weight: 600; color: $text-primary; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dv-type-badge {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: $accent-primary;
  background: rgba(var(--accent-primary-rgb), 0.12);
  border: 1px solid rgba(var(--accent-primary-rgb), 0.25);
  border-radius: 5px;
  padding: 1px 6px;
}

.dv-body { min-height: 120px; }
.dv-loading { padding: 8px 0; }

.dv-error {
  display: flex; align-items: center; gap: 8px;
  color: $error; font-size: 13px;
  background: rgba(var(--error-rgb), 0.06);
  border: 1px solid rgba(var(--error-rgb), 0.2);
  border-radius: 8px; padding: 12px 14px;
}

.dv-meta {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px 14px;
}
.dv-meta-item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.dv-meta-label { font-size: 11px; color: $text-muted; }
.dv-meta-value { font-size: 13px; color: $text-primary; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.dv-divider { height: 1px; background: $border-color; margin: 14px 0 12px; }

.dv-content-label { font-size: 12px; font-weight: 600; color: $text-muted; margin-bottom: 8px; }
.dv-content {
  font-family: $font-code;
  font-size: 12.5px;
  line-height: 1.7;
  color: $text-secondary;
  white-space: pre-wrap;
  word-break: break-word;
  background: $code-bg;
  border: 1px solid $border-light;
  border-radius: 8px;
  padding: 12px 14px;
  margin: 0;
  max-height: 46vh;
}
.dv-empty { font-size: 13px; color: $text-muted; padding: 16px; text-align: center; border: 1px dashed $border-color; border-radius: 8px; }

.dv-footer { display: flex; justify-content: flex-end; }

@media (max-width: $breakpoint-mobile) {
  .dv-meta { grid-template-columns: repeat(2, 1fr); }
}
</style>
