<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { NButton, NInput, useMessage } from 'naive-ui'
import { api } from '@/api/client'

const message = useMessage()
const loading = ref(false)
const saving = ref(false)

const forms = reactive<{ memory: string; user: string; soul: string }>({
  memory: '',
  user: '',
  soul: '',
})

const searchQ = ref('')
const hits = ref<any[]>([])
const searching = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await api.getMemory()
    forms.memory = d.memory || ''
    forms.user = d.user || ''
    forms.soul = d.soul || ''
  } catch (e: any) {
    message.error(e?.message || '加载记忆失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await api.saveMemory({ memory: forms.memory, user: forms.user, soul: forms.soul })
    message.success('已保存')
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function exportMemo() {
  try {
    const d = await api.exportMemory()
    const blob = new Blob([d.combined || ''], { type: 'text/markdown;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = '记忆备份.md'
    a.click()
    URL.revokeObjectURL(a.href)
    message.success('已导出记忆备份')
  } catch (e: any) {
    message.error(e?.message || '导出失败')
  }
}

async function doSearch() {
  const q = searchQ.value.trim()
  if (!q) {
    hits.value = []
    return
  }
  searching.value = true
  try {
    const d = await api.searchMemory(q)
    hits.value = d.hits || []
  } catch (e: any) {
    message.error(e?.message || '搜索失败')
  } finally {
    searching.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="memory-view">
    <header class="page-header">
      <div>
        <div class="header-title">记忆</div>
        <div class="header-sub">长期记忆文件（MEMORY.md / USER.md / SOUL.md），会注入 Agent 上下文，使其长期记住项目背景、用户画像与行为准则。</div>
      </div>
      <div class="head-actions">
        <NInput v-model:value="searchQ" size="small" placeholder="搜索记忆内容..." clearable @keyup.enter="doSearch" style="width: 180px">
          <template #prefix>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          </template>
        </NInput>
        <NButton size="small" :loading="searching" quaternary @click="doSearch">搜索</NButton>
        <NButton size="small" :loading="loading" quaternary @click="load">刷新</NButton>
        <NButton size="small" quaternary @click="exportMemo">导出</NButton>
        <NButton size="small" type="primary" :loading="saving" @click="save">保存</NButton>
      </div>
    </header>

    <div v-if="hits.length" class="search-hits">
      <div class="hits-title">搜索结果（{{ hits.length }}）</div>
      <div v-for="(h, i) in hits" :key="i" class="hit">
        <span class="hit-file">{{ h.file }}:{{ h.line }}</span>
        <span class="hit-text">{{ h.text }}</span>
      </div>
    </div>

    <div class="memory-content">
      <section class="mem-block">
        <h3 class="mem-title">MEMORY.md <span class="mem-hint">全局长期记忆 / 项目背景</span></h3>
        <NInput v-model:value="forms.memory" type="textarea" :autosize="{ minRows: 6, maxRows: 20 }" placeholder="记录长期记忆、偏好、关键事实..." />
      </section>

      <section class="mem-block">
        <h3 class="mem-title">USER.md <span class="mem-hint">用户画像 / 使用习惯</span></h3>
        <NInput v-model:value="forms.user" type="textarea" :autosize="{ minRows: 5, maxRows: 18 }" placeholder="记录用户的信息与偏好..." />
      </section>

      <section class="mem-block">
        <h3 class="mem-title">SOUL.md <span class="mem-hint">行为准则 / 人格设定</span></h3>
        <NInput v-model:value="forms.soul" type="textarea" :autosize="{ minRows: 5, maxRows: 18 }" placeholder="记录智能体的行为准则与人格..." />
      </section>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.memory-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.head-actions { display: flex; gap: 6px; align-items: center; }
.memory-content { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 20px; }

.search-hits { margin: 12px 20px 0; padding: 10px 12px; background: $bg-card; border: 1px solid $border-color; border-radius: $radius-md; }
.hits-title { font-size: 12px; color: $text-muted; margin-bottom: 6px; }
.hit { font-size: 12px; padding: 3px 0; border-top: 1px dashed $border-color; }
.hit:first-child { border-top: none; }
.hit-file { color: $accent-primary; font-family: $font-code; margin-right: 8px; }
.hit-text { color: $text-secondary; }

.mem-block { background: $bg-card; border: 1px solid $border-color; border-radius: $radius-md; padding: 16px; }
.mem-title { font-size: 14px; font-weight: 600; color: $text-primary; margin: 0 0 12px; display: flex; align-items: baseline; gap: 10px; font-family: $font-code; }
.mem-hint { font-size: 12px; font-weight: 400; color: $text-muted; font-family: $font-ui; }
</style>
