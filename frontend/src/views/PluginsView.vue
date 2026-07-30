<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { NModal, NForm, NFormItem, NInput, NSwitch, NButton, NSelect } from 'naive-ui'
import { api } from '@/api/client'
import { actAs } from '@/api/actas'
import { useAppStore } from '@/stores/app'
import ModuleList from '@/components/modules/ModuleList.vue'
import ShareScopeSelector from '@/components/modules/ShareScopeSelector.vue'

const message = useMessage()
const app = useAppStore()
const loading = ref(false)
const plugins = ref<any[]>([])

const showModal = ref(false)
const submitting = ref(false)
const editing = ref<string | null>(null)
const form = reactive<{
  name: string
  description: string
  version: string
  enabled: boolean
  shared: boolean
  share_with: string[]
  tools: any[]
}>({
  name: '',
  description: '',
  version: '0.1',
  enabled: true,
  shared: false,
  share_with: [],
  tools: [],
})

function markEditable(items: any[]) {
  const me = (app.user as any)?.username || ''
  const admin = app.isAdmin
  for (const it of items) it._editable = admin || (!!it.owner && it.owner === me)
  return items
}

function blankTool() {
  return { name: '', description: '', type: 'shell', command: '', url: '', method: 'POST', args_text: '' }
}

async function load() {
  loading.value = true
  try {
    const d = await api.listPlugins()
    plugins.value = markEditable(d.plugins || [])
  } catch (e: any) {
    message.error(e?.message || '加载插件失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', description: '', version: '0.1', enabled: true, shared: false, share_with: [], tools: [blankTool()] })
  showModal.value = true
}

async function openEdit(it: any) {
  try {
    const d = await api.getPlugin(it.name)
    editing.value = it.name
    Object.assign(form, {
      name: d.name,
      description: d.description || '',
      version: d.version || '0.1',
      enabled: d.enabled !== false,
      shared: !!d.shared,
      share_with: d.share_with || [],
      tools: (d.tools && d.tools.length ? d.tools : [blankTool()]).map((t: any) => ({
        name: t.name || '',
        description: t.description || '',
        type: t.type || 'shell',
        command: t.command || '',
        url: t.url || '',
        method: t.method || 'POST',
        args_text: (t.args || []).join('\n'),
      })),
    })
    showModal.value = true
  } catch (e: any) {
    message.error(e?.message || '读取插件失败')
  }
}

function addTool() {
  form.tools.push(blankTool())
}
function removeTool(idx: number) {
  form.tools.splice(idx, 1)
}

function buildTools() {
  return form.tools
    .filter((t) => t.name.trim())
    .map((t) => {
      const o: any = { name: t.name.trim(), description: t.description, type: t.type }
      if (t.type === 'http') {
        o.url = t.url
        o.method = t.method || 'POST'
      } else {
        o.command = t.command
        o.args = t.args_text.split('\n').map((s: string) => s.trim()).filter(Boolean)
      }
      return o
    })
}

async function submit() {
  if (!form.name.trim()) {
    message.warning('请填写插件名称')
    return
  }
  const tools = buildTools()
  if (!tools.length) {
    message.warning('请至少配置一个工具')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.updatePlugin(editing.value, {
        description: form.description,
        version: form.version,
        enabled: form.enabled,
        shared: form.shared,
        share_with: form.share_with,
        tools,
      })
      message.success('已更新')
    } else {
      await api.createPlugin({
        name: form.name.trim(),
        description: form.description,
        version: form.version,
        enabled: form.enabled,
        shared: form.shared,
        share_with: form.share_with,
        tools,
      })
      message.success('已创建')
    }
    showModal.value = false
    await load()
    await api.refreshPlugins().catch(() => {})
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function remove(it: any) {
  try {
    await api.deletePlugin(it.name)
    message.success('已删除')
    await load()
    await api.refreshPlugins().catch(() => {})
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function onToggle(p: { name: string; enabled: boolean }) {
  try {
    await api.togglePlugin(p.name, p.enabled)
    const it = plugins.value.find((s) => s.name === p.name)
    if (it) it.enabled = p.enabled
    await api.refreshPlugins().catch(() => {})
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  }
}

onMounted(load)
watch(actAs, () => load())
</script>

<template>
  <div class="module-view">
    <header class="page-header">
      <div>
        <div class="header-title">插件</div>
        <div class="header-sub">插件可声明若干「自定义工具」（shell 命令 / HTTP 接口）。启用后，这些工具会注册到 Agent，可在对话中直接调用。</div>
      </div>
      <NButton type="primary" size="small" @click="openCreate">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </template>
        新建插件
      </NButton>
    </header>

    <div class="module-content">
      <ModuleList
        title="插件"
        :items="plugins"
        :loading="loading"
        :editable="true"
        empty-text="暂无插件，点击右上角「新建插件」"
        search-placeholder="搜索插件..."
        @toggle="onToggle"
        @refresh="load"
        @edit="openEdit"
        @delete="remove"
      />
    </div>

    <NModal v-model:show="showModal" :title="editing ? '编辑插件' : '新建插件'" preset="card" style="width: 720px; max-width: 94vw;">
      <NForm>
        <NFormItem label="名称">
          <NInput v-model:value="form.name" :disabled="!!editing" placeholder="插件目录名（英文/数字/.-_）" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="form.description" placeholder="一句话描述" />
        </NFormItem>
        <NFormItem label="版本">
          <NInput v-model:value="form.version" placeholder="0.1" />
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="form.enabled" />
        </NFormItem>
        <NFormItem label="共享范围">
          <ShareScopeSelector v-model:shared="form.shared" v-model:share-with="form.share_with" />
        </NFormItem>

        <div class="tools-head">
          <span>工具列表</span>
          <NButton size="tiny" dashed @click="addTool">+ 添加工具</NButton>
        </div>

        <div v-for="(t, idx) in form.tools" :key="idx" class="tool-row">
          <div class="tool-row-head">
            <span>工具 #{{ idx + 1 }}</span>
            <NButton size="tiny" text type="error" @click="removeTool(idx)">移除</NButton>
          </div>
          <div class="tool-grid">
            <NFormItem label="名称" class="fg">
              <NInput v-model:value="t.name" placeholder="tool_name" />
            </NFormItem>
            <NFormItem label="类型" class="fg">
              <NSelect v-model:value="t.type" :options="[{label:'Shell 命令',value:'shell'},{label:'HTTP 接口',value:'http'}]" />
            </NFormItem>
          </div>
          <NFormItem label="描述">
            <NInput v-model:value="t.description" placeholder="工具用途说明" />
          </NFormItem>
          <template v-if="t.type === 'shell'">
            <NFormItem label="命令">
              <NInput v-model:value="t.command" placeholder="可执行文件，如 python / cmd" />
            </NFormItem>
            <NFormItem label="参数（每行一个，支持 {{变量}}）">
              <NInput v-model:value="t.args_text" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" placeholder="第一行\n第二行" />
            </NFormItem>
          </template>
          <template v-else>
            <NFormItem label="URL">
              <NInput v-model:value="t.url" placeholder="https://... 或 http://127.0.0.1:8080/..." />
            </NFormItem>
            <NFormItem label="方法">
              <NSelect v-model:value="t.method" :options="['GET','POST','PUT','DELETE'].map((m) => ({ label: m, value: m }))" />
            </NFormItem>
          </template>
        </div>
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
.module-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.module-content { flex: 1; overflow-y: auto; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; }

.tools-head { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 10px; font-weight: 600; color: $text-primary; }
.tool-row { border: 1px solid $border-color; border-radius: $radius-md; padding: 12px; margin-bottom: 12px; background: $bg-card; }
.tool-row-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: $text-secondary; margin-bottom: 8px; }
.tool-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.fg { margin-bottom: 8px; }
.share-hint { font-size: 12px; color: $text-muted; margin-left: 10px; }
</style>
