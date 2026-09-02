<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { NButton, NInput, NTag, NAlert, useMessage } from 'naive-ui'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'

const message = useMessage()
const app = useAppStore()
/** 只读访客（仅 modules:read）隐藏保存操作，避免点击后 403 */
const canWrite = computed(() => app.can('modules:write'))
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

// 向量长期记忆（体量 / 清空）—— 后端能力补齐前端入口（MemoryView 此前只能编辑 3 个 md 文件）
const vecStats = ref<any>(null)
const vecLoading = ref(false)
const vecClearing = ref(false)

async function loadVector() {
  vecLoading.value = true
  try {
    vecStats.value = await api.getVectorStats()
  } catch { vecStats.value = null } finally { vecLoading.value = false }
}

async function clearVector() {
  if (!app.isAdmin && !(vecStats.value?.count)) return
  const scope = app.isAdmin ? '全部用户的向量记忆' : '你的向量记忆'
  const ok = window.confirm(`确认清空${scope}？此操作不可恢复。`)
  if (!ok) return
  vecClearing.value = true
  try {
    const r = await api.clearVectorMemory()
    message.success(`已清空向量记忆（删除 ${r?.deleted ?? 0} 条）`)
    await loadVector()
  } catch (e: any) {
    message.error(e?.message || '清空失败')
  } finally { vecClearing.value = false }
}

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

onMounted(() => { load(); loadVector() })
</script>

<template>
  <div class="memory-view">
    <header class="page-header">
      <div>
        <div class="header-title">记忆</div>
        <div class="header-sub">长期记忆文件（MEMORY.md / USER.md / SOUL.md），会注入 Agent 上下文，使其长期记住项目背景、用户画像与行为准则。</div>
      </div>
      <div class="head-actions">
        <NTag v-if="!canWrite" size="small" type="default" :bordered="false">只读模式</NTag>
        <NInput v-model:value="searchQ" size="small" placeholder="搜索记忆内容..." clearable @keyup.enter="doSearch" style="width: 180px">
          <template #prefix>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          </template>
        </NInput>
        <NButton size="small" :loading="searching" quaternary @click="doSearch">搜索</NButton>
        <NButton size="small" :loading="loading" quaternary @click="load">刷新</NButton>
        <NButton size="small" quaternary @click="exportMemo">导出</NButton>
        <NButton v-if="canWrite" size="small" type="primary" :loading="saving" @click="save">保存</NButton>
      </div>
    </header>

    <div v-if="hits.length" class="search-hits">
      <div class="hits-title">搜索结果（{{ hits.length }}）</div>
      <div v-for="(h, i) in hits" :key="i" class="hit">
        <span class="hit-file">{{ h.file }}:{{ h.line }}</span>
        <span class="hit-text">{{ h.text }}</span>
      </div>
    </div>

    <!-- 向量长期记忆（体量 / 清空） -->
    <NAlert
      v-if="vecStats && vecStats.unconfigured"
      type="warning"
      :show-icon="true"
      title="未配置 Embedding 模型，记忆检索已自动回退为全文检索"
      style="margin-bottom: 12px"
    >
      未配置语义 Embedding，系统未静默降级为无意义的哈希伪向量，而是自动以
      <b>全文检索（FTS5 中文子串匹配）</b>作为检索主干。配置
      <code>embedding.embed_model</code> 后将自动升级为「全文 + 向量」混合检索。
    </NAlert>
    <div class="vec-card">
      <div class="vec-title">向量长期记忆</div>
      <div class="vec-body">
        <template v-if="vecLoading"><span class="vec-muted">加载中…</span></template>
        <template v-else-if="vecStats">
          <span class="vec-item">后端：<b>{{ vecStats.backend || '—' }}</b></span>
          <span class="vec-item">向量条数：<b>{{ vecStats.vectors ?? 0 }}</b></span>
          <span class="vec-item">维度：<b>{{ vecStats.embedding_dim ?? '—' }}</b></span>
          <span class="vec-item">文档：<b>{{ vecStats.documents ?? 0 }}</b></span>
          <span class="vec-item">归属：<b>{{ app.isAdmin ? (vecStats.owner || '全部') : (app.user?.user || '我') }}</b></span>
          <NButton size="tiny" type="error" quaternary :loading="vecClearing" @click="clearVector">
            清空{{ app.isAdmin ? '（全部）' : '我的' }}向量记忆
          </NButton>
        </template>
        <template v-else>
          <span class="vec-muted">向量记忆未启用或不可用（需 memory.vector_enabled=true 且后端就绪）</span>
        </template>
      </div>
    </div>

    <div class="memory-content">
      <section class="mem-block">
        <h3 class="mem-title">MEMORY.md <span class="mem-hint">全局长期记忆 / 项目背景</span></h3>
        <NInput v-model:value="forms.memory" type="textarea" :autosize="{ minRows: 6, maxRows: 20 }" :disabled="!canWrite" placeholder="记录长期记忆、偏好、关键事实..." />
      </section>

      <section class="mem-block">
        <h3 class="mem-title">USER.md <span class="mem-hint">用户画像 / 使用习惯</span></h3>
        <NInput v-model:value="forms.user" type="textarea" :autosize="{ minRows: 5, maxRows: 18 }" :disabled="!canWrite" placeholder="记录用户的信息与偏好..." />
      </section>

      <section class="mem-block">
        <h3 class="mem-title">SOUL.md <span class="mem-hint">行为准则 / 人格设定</span></h3>
        <NInput v-model:value="forms.soul" type="textarea" :autosize="{ minRows: 5, maxRows: 18 }" :disabled="!canWrite" placeholder="记录智能体的行为准则与人格..." />
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
.vec-card { margin: 12px 20px 0; background: $bg-card; border: 1px solid $border-color; border-radius: $radius-md; padding: 12px 14px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.vec-title { font-size: 13px; font-weight: 600; color: $text-primary; }
.vec-body { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: 12px; color: $text-secondary; }
.vec-item b { color: $text-primary; font-family: $font-code; }
.vec-muted { color: $text-muted; }
.mem-title { font-size: 14px; font-weight: 600; color: $text-primary; margin: 0 0 12px; display: flex; align-items: baseline; gap: 10px; font-family: $font-code; }
.mem-hint { font-size: 12px; font-weight: 400; color: $text-muted; font-family: $font-ui; }
</style>
