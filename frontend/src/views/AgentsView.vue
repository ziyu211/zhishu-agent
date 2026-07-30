<script setup lang="ts">
import { ref, reactive, onMounted, computed, watch } from 'vue'
import { useMessage } from 'naive-ui'
import {
  NModal, NForm, NFormItem, NInput, NSwitch, NButton, NRadioGroup, NRadioButton, NSelect, NTag,
} from 'naive-ui'
import { api } from '@/api/client'
import { actAs } from '@/api/actas'
import { useAppStore } from '@/stores/app'
import ShareScopeSelector from '@/components/modules/ShareScopeSelector.vue'

const message = useMessage()
const app = useAppStore()
const loading = ref(false)
const agents = ref<any[]>([])

function canEditItem(it: any): boolean {
  const me = (app.user as any)?.username || ''
  return app.isAdmin || (!!it.owner && it.owner === me)
}

const showModal = ref(false)
const submitting = ref(false)
const editing = ref<string | null>(null)

// 工具/模型选项（打开模态时加载）
const toolOptions = ref<any[]>([])
const modelOptions = ref<any[]>([])

const form = reactive<{
  name: string
  description: string
  version: string
  enabled: boolean
  system_prompt: string
  model: string | null
  toolsMode: 'all' | 'none' | 'custom'
  tools: string[]
  max_steps: string | null
  shared: boolean
  share_with: string[]
}>({
  name: '',
  description: '',
  version: '1.0.0',
  enabled: true,
  system_prompt: '',
  model: null,
  toolsMode: 'all',
  tools: [],
  max_steps: null,
  shared: false,
  share_with: [],
})

async function load() {
  loading.value = true
  try {
    const d = await api.listAgents()
    agents.value = d.agents || []
  } catch (e: any) {
    message.error(e?.message || '加载智能体失败')
  } finally {
    loading.value = false
  }
}

async function loadMeta() {
  try {
    const [t, m] = await Promise.all([api.listTools(), api.listModels()])
    toolOptions.value = (t.tools || []).map((x: any) => ({ label: x.name, value: x.name }))
    const opts: any[] = [{ label: '默认（随对话模型）', value: '' }]
    for (const p of m.providers || []) {
      for (const md of p.models || []) opts.push({ label: `${p.label} / ${md}`, value: `${p.provider}/${md}` })
    }
    modelOptions.value = opts
  } catch {
    /* 静默 */
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    name: '',
    description: '',
    version: '1.0.0',
    enabled: true,
    system_prompt: '',
    model: null,
    toolsMode: 'all',
    tools: [],
    max_steps: null,
    shared: false,
    share_with: [],
  })
  showModal.value = true
}

async function openEdit(it: any) {
  try {
    const d = await api.getAgent(it.name)
    editing.value = it.name
    const rawTools = d.tools
    let mode: 'all' | 'none' | 'custom' = 'all'
    let custom: string[] = []
    if (rawTools === 'none' || (Array.isArray(rawTools) && rawTools.length === 0)) mode = 'none'
    else if (Array.isArray(rawTools)) { mode = 'custom'; custom = rawTools }
    Object.assign(form, {
      name: d.name,
      description: d.description || '',
      version: d.version || '1.0.0',
      enabled: d.enabled !== false,
      system_prompt: d.system_prompt || '',
      model: d.model || null,
      toolsMode: mode,
      tools: custom,
      max_steps: d.max_steps ?? null,
      shared: !!d.shared,
      share_with: d.share_with || [],
    })
    showModal.value = true
  } catch (e: any) {
    message.error(e?.message || '读取智能体失败')
  }
}

function buildPayload() {
  const tools =
    form.toolsMode === 'all' ? 'all' : form.toolsMode === 'none' ? 'none' : form.tools
  let ms: number | null = null
  if (form.max_steps !== null && form.max_steps !== undefined && String(form.max_steps).trim() !== '') {
    const n = parseInt(String(form.max_steps), 10)
    if (!isNaN(n) && n > 0) ms = n
  }
  return {
    description: form.description,
    version: form.version,
    enabled: form.enabled,
    system_prompt: form.system_prompt,
    model: form.model || null,
    tools,
    max_steps: ms,
    shared: form.shared,
    share_with: form.share_with,
  }
}

async function submit() {
  if (!form.name.trim()) {
    message.warning('请填写名称')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.updateAgent(editing.value, buildPayload())
      message.success('已更新')
    } else {
      await api.createAgent({ name: form.name.trim(), ...buildPayload() })
      message.success('已创建')
    }
    showModal.value = false
    await load()
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function remove(it: any) {
  try {
    await api.deleteAgent(it.name)
    message.success('已删除')
    await load()
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function onToggle(p: { name: string; enabled: boolean }) {
  try {
    await api.toggleAgent(p.name, p.enabled)
    const it = agents.value.find((s) => s.name === p.name)
    if (it) it.enabled = p.enabled
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  }
}

const toolsLabel = (a: any) => {
  const t = a.tools
  if (t === 'all' || t === undefined || t === null) return '全部工具'
  if (t === 'none' || (Array.isArray(t) && t.length === 0)) return '无工具'
  if (Array.isArray(t)) return `${t.length} 个指定工具`
  return String(t)
}

const placeholderPrompt = computed(() =>
  '描述该子智能体的人设与专长，例如：\n你是一位严谨的翻译专家，只负责中英互译，遇到歧义时给出两种译法并说明取舍。',
)

onMounted(() => {
  load()
  loadMeta()
})
watch(actAs, () => load())
</script>

<template>
  <div class="agents-view">
    <header class="page-header">
      <div>
        <div class="header-title">智能体</div>
        <div class="header-sub">
          多 Agent 协作成员（主管-成员模式）。主管在对话时可<span class="hl">自动委派</span>子智能体协同处理；
          也可在聊天页顶栏直接选定某个子智能体对话。每个子智能体拥有独立人设、可选模型与工具范围。
        </div>
      </div>
      <NButton type="primary" size="small" @click="openCreate">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </template>
        新建智能体
      </NButton>
    </header>

    <div class="agents-content">
      <div v-if="!agents.length && !loading" class="empty-wrap">暂无智能体，点击右上角「新建智能体」</div>
      <div v-else class="card-grid">
        <div v-for="a in agents" :key="a.name" class="agent-card" :class="{ off: a.enabled === false }">
          <div class="card-main">
            <div class="card-title-row">
              <span class="card-name">{{ a.name }}</span>
              <span v-if="a.version" class="ver-tag">{{ a.version }}</span>
              <span class="tool-tag">{{ a.tool_count ?? 0 }} 工具</span>
              <span v-if="a.model" class="model-tag">{{ a.model }}</span>
              <NTag v-if="a.shared" size="tiny" type="info" :bordered="false">共享·全员</NTag>
              <NTag v-else-if="a.share_with && a.share_with.length" size="tiny" type="warning" :bordered="false">共享·按角色</NTag>
              <NTag v-else-if="a.owner" size="tiny" :bordered="false">{{ a.owner }}</NTag>
              <NTag v-else size="tiny" :bordered="false">公共</NTag>
            </div>
            <div class="card-desc">{{ a.description || '暂无描述' }}</div>
            <div class="card-meta-line">
              <span class="meta-chip">工具：{{ toolsLabel(a) }}</span>
              <span class="meta-chip">模型：{{ a.model || '默认' }}</span>
              <span class="meta-chip">步数：{{ a.max_steps || '默认' }}</span>
            </div>
          </div>
          <div class="card-right">
            <div class="card-actions" v-if="canEditItem(a)">
              <NButton size="tiny" quaternary type="primary" @click="openEdit(a)">编辑</NButton>
              <NButton size="tiny" quaternary type="error" @click="remove(a)">删除</NButton>
            </div>
            <NSwitch :value="a.enabled !== false" :disabled="!canEditItem(a)" @update:value="(v: boolean) => onToggle({ name: a.name, enabled: v })" />
          </div>
        </div>
      </div>
    </div>

    <NModal v-model:show="showModal" :title="editing ? '编辑智能体' : '新建智能体'" preset="card" style="width: 680px; max-width: 94vw;">
      <NForm>
        <NFormItem label="名称（目录名，英文/数字/.-_）">
          <NInput v-model:value="form.name" :disabled="!!editing" placeholder="如 translator / coder / summarizer" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="form.description" placeholder="一句话描述专长" />
        </NFormItem>
        <NFormItem label="版本">
          <NInput v-model:value="form.version" placeholder="1.0.0" />
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="form.enabled" />
        </NFormItem>
        <NFormItem label="共享范围">
          <ShareScopeSelector v-model:shared="form.shared" v-model:share-with="form.share_with" />
        </NFormItem>
        <NFormItem label="人设 / 系统提示词">
          <NInput
            v-model:value="form.system_prompt"
            type="textarea"
            :autosize="{ minRows: 6, maxRows: 16 }"
            :placeholder="placeholderPrompt"
          />
        </NFormItem>
        <NFormItem label="模型覆盖">
          <NSelect
            v-model:value="form.model"
            :options="modelOptions"
            placeholder="默认（使用对话所选模型）"
            clearable
            filterable
          />
        </NFormItem>
        <NFormItem label="工具范围">
          <div class="tools-block">
            <NRadioGroup v-model:value="form.toolsMode" size="small">
              <NRadioButton value="all">全部工具</NRadioButton>
              <NRadioButton value="none">无工具</NRadioButton>
              <NRadioButton value="custom">自定义</NRadioButton>
            </NRadioGroup>
            <NSelect
              v-if="form.toolsMode === 'custom'"
              v-model:value="form.tools"
              :options="toolOptions"
              multiple
              filterable
              placeholder="选择该子智能体可调用的工具"
              style="margin-top: 8px"
            />
          </div>
        </NFormItem>
        <NFormItem label="最大推理步数（可选）">
          <NInput
            v-model:value="form.max_steps"
            type="text"
            placeholder="留空=默认(8)"
            style="max-width: 160px"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <div class="modal-footer">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" :loading="submitting" @click="submit">保存</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.agents-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.agents-content { flex: 1; overflow-y: auto; padding: 20px; }

.page-header {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
  padding: 20px 24px 12px;
  .header-title { font-size: 18px; font-weight: 600; color: $text-primary; }
  .header-sub { font-size: 13px; color: $text-muted; margin-top: 4px; max-width: 760px; line-height: 1.6; }
  .hl { color: $accent-primary; font-weight: 600; }
}

.empty-wrap { padding: 64px 0; text-align: center; color: $text-muted; font-size: 14px; }

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px;
}

.agent-card {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 14px 16px; background: $bg-card; border: 1px solid $border-color; border-radius: $radius-md;
  transition: border-color $transition-fast;
  &:hover { border-color: $accent-muted; }
  &.off { opacity: 0.62; }
}

.card-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-name { font-size: 14px; font-weight: 600; color: $text-primary; }
.ver-tag, .tool-tag, .model-tag {
  font-family: $font-code; font-size: 11px; color: $text-secondary;
  background: $code-bg; border: 1px solid $border-light; border-radius: 4px; padding: 0 6px; line-height: 16px;
}
.model-tag { color: $accent-primary; border-color: rgba(var(--accent-primary-rgb), 0.3); }

.card-desc { font-size: 12px; color: $text-muted; margin-top: 6px; line-height: 1.5; max-height: 3em; overflow: hidden; }
.card-meta-line { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.meta-chip { font-size: 11px; color: $text-secondary; background: $code-bg; border: 1px solid $border-light; border-radius: 4px; padding: 1px 8px; }

.card-right { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; flex-shrink: 0; }
.card-actions { display: flex; gap: 2px; }

.tools-block { display: flex; flex-direction: column; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; }
.share-hint { font-size: 12px; color: $text-muted; margin-left: 10px; }
</style>
