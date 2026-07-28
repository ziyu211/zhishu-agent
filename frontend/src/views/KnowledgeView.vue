<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NButton, NInput, NModal, NTag, NEmpty, NSpin, NUpload, useMessage, NTabs, NTabPane,
  NSwitch, NTooltip, NScrollbar,
} from 'naive-ui'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'
import KnowledgeGraph from '@/components/knowledge/KnowledgeGraph.vue'

const message = useMessage()
const appStore = useAppStore()
const isAdmin = computed(() => appStore.isAdmin)
const tab = ref<'kb' | 'graph'>('kb')

const stats = ref<any>({ documents: 0, vectors: 0, backend: '—', embedding_dim: 0 })
const docs = ref<any[]>([])
const loading = ref(false)
const scope = ref<'mine' | 'all'>('mine')
const filter = ref('')
const reparseId = ref('')

const ingestText = ref('')
const docTitle = ref('')
const uploading = ref(false)
const dragging = ref(false)
const pending = ref<{ name: string; size: number; status: string }[]>([])

const query = ref('')
const results = ref<any[]>([])
const searching = ref(false)

const previewDoc = ref<any>(null)
const previewOpen = ref(false)
const previewLoading = ref(false)

async function loadStats() {
  try { stats.value = await api.knowledgeStats() } catch { /* 静默 */ }
}
async function loadDocs() {
  loading.value = true
  try {
    const r = await api.listDocuments(scope.value, filter.value.trim() || undefined)
    docs.value = r.documents || []
  } catch (e: any) {
    message.error(e?.message || '加载文档失败')
  } finally {
    loading.value = false
  }
}
function onScope(v: boolean) {
  scope.value = v ? 'all' : 'mine'
  loadDocs()
}

function fmtSize(n?: number) {
  if (!n) return '0 B'
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}
function fmtTime(t?: number) {
  if (!t) return '—'
  const d = new Date(t * 1000)
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function ingest() {
  const t = ingestText.value.trim()
  if (!t) { message.error('请输入文本'); return }
  api.ingestText(t, undefined, docTitle.value.trim() || undefined)
    .then((r) => {
      if (r?.ok === false) { message.error(r?.msg || '入库失败'); return }
      message.success(r?.skipped ? '内容为空，已跳过' : '已入库')
      ingestText.value = ''; docTitle.value = ''
      loadStats(); loadDocs()
    })
    .catch((e: any) => message.error(e?.message || '入库失败'))
}

function onFiles(files: FileList | File[]) {
  const arr = Array.from(files)
  if (!arr.length) return
  uploadFiles(arr)
}
async function uploadFiles(arr: File[]) {
  uploading.value = true
  pending.value = arr.map((f) => ({ name: f.name, size: f.size, status: '等待中' }))
  let ok = 0, fail = 0
  for (let i = 0; i < arr.length; i++) {
    const f = arr[i]
    pending.value[i].status = '上传中…'
    try {
      const r = await api.uploadFile(f)
      if (r?.ok) { pending.value[i].status = `成功（${r.chunks} 块）`; ok++ }
      else { pending.value[i].status = r?.msg || '失败'; fail++ }
    } catch (e: any) {
      pending.value[i].status = e?.message || '失败'; fail++
    }
  }
  uploading.value = false
  if (ok) { message.success(`已上传 ${ok} 个文件`); loadStats(); loadDocs() }
  if (fail) message.warning(`${fail} 个文件失败，详见列表`)
}

function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) onFiles(input.files)
  input.value = ''
}
function onDrop(e: DragEvent) {
  dragging.value = false
  if (e.dataTransfer?.files?.length) onFiles(e.dataTransfer.files)
}

async function openPreview(doc: any) {
  previewDoc.value = doc
  previewOpen.value = true
  previewLoading.value = true
  try {
    const full = await api.getDocument(doc.doc_id)
    previewDoc.value = { ...doc, content: full?.content || '' }
  } catch (e: any) {
    message.error(e?.message || '加载失败')
  } finally {
    previewLoading.value = false
  }
}
function delDoc(doc: any) {
  api.deleteDocument(doc.doc_id)
    .then(() => { message.success('已删除'); loadStats(); loadDocs() })
    .catch((e: any) => message.error(e?.message || '删除失败'))
}

async function reparseDoc(doc: any) {
  if (!doc.raw_path) { message.warning('该文档未保留原始文件，无法重新解析'); return }
  reparseId.value = doc.doc_id
  try {
    const r = await api.reparseDocument(doc.doc_id)
    if (r?.ok) message.success(`已重新解析（${r.chunks ?? 0} 块）`)
    else message.error('重新解析失败')
    loadStats(); loadDocs()
  } catch (e: any) {
    message.error(e?.message || '重新解析失败')
  } finally {
    reparseId.value = ''
  }
}

function search() {
  const q = query.value.trim()
  if (!q) return
  searching.value = true
  api.searchKnowledge(q, 8)
    .then((r) => (results.value = r.hits || r.results || r || []))
    .catch((e: any) => message.error(e?.message || '检索失败'))
    .finally(() => (searching.value = false))
}

onMounted(() => { loadStats(); loadDocs() })
</script>

<template>
  <div class="knowledge-view">
    <header class="page-header">
      <div>
        <div class="header-title">知识库</div>
        <div class="header-sub">本地向量检索 · 对话时自动增强（RAG）</div>
      </div>
      <div class="stat-pills">
        <span class="pill">{{ stats.documents }} 文档</span>
        <span class="pill">{{ stats.vectors }} 向量</span>
        <span class="pill">{{ stats.backend }}</span>
      </div>
    </header>

    <NTabs v-model:value="tab" type="line" class="kg-tabs">
      <NTabPane name="kb" tab="知识库">
    <div class="kb-body">
      <NScrollbar style="max-height: calc(100vh - 160px)">
        <!-- 导入区 -->
        <div class="grid-2">
          <section class="card-block">
            <h3 class="block-title">导入文本</h3>
            <NInput v-model:value="ingestText" type="textarea" :autosize="{ minRows: 4, maxRows: 10 }" placeholder="粘贴要入库的文本…" />
            <div class="row-between">
              <NInput v-model:value="docTitle" placeholder="文档标题（可选）" size="small" style="width: 240px" />
              <NButton type="primary" size="small" @click="ingest">入库</NButton>
            </div>
          </section>

          <section class="card-block">
            <h3 class="block-title">上传文件</h3>
            <div
              class="dropzone"
              :class="{ drag: dragging }"
              @dragover.prevent="dragging = true"
              @dragleave.prevent="dragging = false"
              @drop.prevent="onDrop"
              @click="($event.target as HTMLElement).querySelector('input')?.click()"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              <span>{{ uploading ? '上传中…' : '点击或拖拽文件到此处（可多选）' }}</span>
              <input type="file" hidden multiple @change="onFileInput" />
            </div>
            <div class="hint-small">支持 TXT / MD / CSV / JSON / 代码 / 日志等文本；PDF / DOCX / XLSX 需安装解析库</div>
            <div v-if="pending.length" class="pending-list">
              <div v-for="(p, i) in pending" :key="i" class="pending-item">
                <span class="pi-name">{{ p.name }}</span>
                <span class="pi-size">{{ fmtSize(p.size) }}</span>
                <span class="pi-status" :class="{ ok: p.status.startsWith('成功'), fail: p.status === '失败' }">{{ p.status }}</span>
              </div>
            </div>
          </section>
        </div>

        <!-- 文档列表 -->
        <section class="card-block">
          <div class="list-head">
            <h3 class="block-title" style="margin:0">已入库文档（{{ docs.length }}）</h3>
            <div class="filter-box">
              <NInput v-model:value="filter" size="small" placeholder="按标题/来源筛选" clearable style="width: 200px" @keydown.enter="loadDocs" @clear="loadDocs" />
              <NButton size="small" :loading="loading" quaternary @click="loadDocs">筛选</NButton>
            </div>
            <div v-if="isAdmin" class="scope-toggle">
              <span class="scope-label">仅我的</span>
              <NSwitch :value="scope === 'all'" @update:value="onScope" />
              <span class="scope-label">全部</span>
            </div>
            <NButton size="small" :loading="loading" quaternary @click="loadDocs">刷新</NButton>
          </div>

          <NSpin :show="loading">
            <div v-if="docs.length" class="doc-table">
              <div class="doc-row doc-head">
                <span class="c-title">标题 / 来源</span>
                <span class="c-type">类型</span>
                <span class="c-num">分块</span>
                <span class="c-size">大小</span>
                <span class="c-time">入库时间</span>
                <span v-if="isAdmin && scope === 'all'" class="c-owner">归属</span>
                <span class="c-act">操作</span>
              </div>
              <div v-for="d in docs" :key="d.doc_id" class="doc-row">
                <span class="c-title">
                  <div class="t-main">{{ d.title }}</div>
                  <div class="t-sub">{{ d.source }}</div>
                </span>
                <span class="c-type"><NTag size="small" :bordered="false">{{ d.file_type || 'TEXT' }}</NTag></span>
                <span class="c-num">{{ d.chunk_count }}</span>
                <span class="c-size">{{ fmtSize(d.size_bytes) }}</span>
                <span class="c-time">{{ fmtTime(d.created_at) }}</span>
                <span v-if="isAdmin && scope === 'all'" class="c-owner">
                  <NTag size="small" :type="d.owner ? 'default' : 'warning'" :bordered="false">{{ d.owner || '共享' }}</NTag>
                </span>
                <span class="c-act">
                  <NButton size="tiny" tertiary @click="openPreview(d)">预览</NButton>
                  <NButton v-if="d.raw_path" size="tiny" tertiary type="warning" :loading="reparseId === d.doc_id" @click="reparseDoc(d)">重解析</NButton>
                  <NButton size="tiny" tertiary type="error" @click="delDoc(d)">删除</NButton>
                </span>
              </div>
            </div>
            <NEmpty v-else-if="!loading" description="还没有文档，上传或粘贴文本开始构建知识库" />
          </NSpin>
        </section>

        <!-- 检索 -->
        <section class="card-block">
          <h3 class="block-title">检索验证</h3>
          <div class="row-between">
            <NInput v-model:value="query" placeholder="输入检索问题，验证知识库命中…" size="small" style="flex: 1" @keydown.enter="search" />
            <NButton size="small" :loading="searching" @click="search">检索</NButton>
          </div>
          <div v-if="results.length" class="result-list">
            <div v-for="(r, i) in results" :key="i" class="result-item">
              <div class="result-score">相似度 {{ (r.score ?? 0).toFixed ? (r.score * 100).toFixed(0) + '%' : r.score }} · 来源 {{ r.meta?.source || r.doc_id }}</div>
              <div class="result-text">{{ r.text || r.content || '—' }}</div>
            </div>
          </div>
          <div v-else-if="!searching" class="hint-empty">暂无检索结果</div>
        </section>
      </NScrollbar>
    </div>

    <!-- 预览弹窗 -->
    <NModal v-model:show="previewOpen" preset="card" title="文档预览" style="width: 720px; max-width: 92vw" :bordered="false">
      <NSpin :show="previewLoading">
        <div v-if="previewDoc" class="preview-meta">
          <NTag size="small" :bordered="false">{{ previewDoc.file_type || 'TEXT' }}</NTag>
          <span>{{ previewDoc.chunk_count }} 块</span>
          <span>{{ fmtSize(previewDoc.size_bytes) }}</span>
          <span>{{ fmtTime(previewDoc.created_at) }}</span>
          <span v-if="previewDoc.owner">归属：{{ previewDoc.owner }}</span>
        </div>
        <pre class="preview-content">{{ previewDoc?.content || '（无预览内容）' }}</pre>
      </NSpin>
    </NModal>
      </NTabPane>
      <NTabPane name="graph" tab="知识图谱">
        <KnowledgeGraph />
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.knowledge-view { height: 100%; display: flex; flex-direction: column; }
.page-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid $border-color; background: $bg-card;
}
.header-title { font-size: 16px; font-weight: 600; color: $text-primary; }
.header-sub { font-size: 12px; color: $text-muted; margin-top: 2px; }
.stat-pills { display: flex; gap: 8px; }
.pill { font-size: 12px; padding: 3px 10px; border-radius: 10px; background: $bg-secondary; color: $text-secondary; border: 1px solid $border-light; }

.kb-body { flex: 1; padding: 20px; overflow: hidden; }
.kg-tabs { padding: 0 20px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 860px) { .grid-2 { grid-template-columns: 1fr; } }

.card-block { background: $bg-card; border: 1px solid $border-color; border-radius: $radius-md; padding: 18px; display: flex; flex-direction: column; gap: 12px; margin-bottom: 20px; }
.block-title { font-size: 14px; font-weight: 600; color: $text-primary; margin: 0; }
.row-between { display: flex; align-items: center; gap: 10px; }

.dropzone {
  border: 1px dashed $border-color; border-radius: $radius-md; padding: 24px;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  color: $text-muted; font-size: 13px; cursor: pointer; transition: all $transition-fast;
  &:hover, &.drag { border-color: $accent-muted; color: $text-secondary; background: rgba(var(--accent-primary-rgb), 0.03); }
}
.hint-small { font-size: 11px; color: $text-muted; }
.pending-list { display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
.pending-item { display: flex; gap: 10px; align-items: center; font-size: 12px; }
.pi-name { flex: 1; color: $text-secondary; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pi-size { color: $text-muted; }
.pi-status { color: $text-muted; &.ok { color: $success; } &.fail { color: $error; } }

.list-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.filter-box { display: flex; align-items: center; gap: 6px; }
.scope-toggle { display: flex; align-items: center; gap: 6px; margin-left: auto; }
.scope-label { font-size: 12px; color: $text-muted; }

.doc-table { display: flex; flex-direction: column; border: 1px solid $border-light; border-radius: $radius-sm; overflow: hidden; }
.doc-row { display: grid; grid-template-columns: minmax(0,2.4fr) 0.7fr 0.6fr 0.8fr 1.3fr 0.8fr 1.4fr; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid $border-light; font-size: 13px; }
.doc-row:last-child { border-bottom: none; }
.doc-head { background: $bg-secondary; color: $text-muted; font-size: 12px; font-weight: 600; }
.doc-row:not(.doc-head):hover { background: rgba(var(--accent-primary-rgb), 0.03); }
.t-main { color: $text-primary; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.t-sub { color: $text-muted; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-act { display: flex; gap: 6px; justify-content: flex-end; }
@media (max-width: 860px) {
  .doc-row { grid-template-columns: 1.6fr 0.8fr 1fr; }
  .doc-row .c-type, .doc-row .c-num, .doc-row .c-size, .doc-row .c-time, .doc-row .c-owner { display: none; }
}

.result-list { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
.result-item { border: 1px solid $border-light; border-radius: $radius-sm; padding: 10px 12px; background: $bg-secondary; }
.result-score { font-size: 11px; color: $text-muted; margin-bottom: 4px; }
.result-text { font-size: 13px; color: $text-secondary; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.hint-empty { color: $text-muted; font-size: 13px; padding: 12px 0; }

.preview-meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; color: $text-muted; margin-bottom: 10px; }
.preview-content { max-height: 50vh; overflow: auto; white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.6; color: $text-secondary; background: $bg-secondary; padding: 12px; border-radius: $radius-sm; margin: 0; }
</style>
