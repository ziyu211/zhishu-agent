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
const servers = ref<any[]>([])

const showCreate = ref(false)
const submitting = ref(false)
const editing = ref<string | null>(null)
const form = reactive<{
  name: string
  description: string
  version: string
  enabled: boolean
  shared: boolean
  share_with: string[]
  command: string
  args_text: string
  env_text: string
}>({
  name: '',
  description: '',
  version: '1.0.0',
  enabled: true,
  shared: false,
  share_with: [],
  command: '',
  args_text: '',
  env_text: '{}',
})

function markEditable(items: any[]) {
  const me = (app.user as any)?.username || ''
  const admin = app.isAdmin
  for (const it of items) it._editable = admin || (!!it.owner && it.owner === me)
  return items
}

// 详情 / 测试
const showDetail = ref(false)
const detail = ref<any>(null)
const toolNames = ref<string[]>([])
const testTool = ref('')
const testArgs = ref('{}')
const testResult = ref('')
const testing = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await api.listMcp()
    servers.value = markEditable(d.servers || [])
  } catch (e: any) {
    message.error(e?.message || '加载 MCP 失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', description: '', version: '1.0.0', enabled: true, shared: false, share_with: [], command: '', args_text: '', env_text: '{}' })
  showCreate.value = true
}

async function openEdit(it: any) {
  try {
    const d = await api.getMcp(it.name)
    editing.value = it.name
    Object.assign(form, {
      name: d.name,
      description: d.description || '',
      version: d.version || '1.0.0',
      enabled: d.enabled !== false,
      shared: !!d.shared,
      share_with: d.share_with || [],
      command: d.command || '',
      args_text: (d.args || []).join('\n'),
      env_text: JSON.stringify(d.env || {}, null, 2),
    })
    showCreate.value = true
  } catch (e: any) {
    message.error(e?.message || '读取失败')
  }
}

async function submit() {
  if (!form.name.trim()) {
    message.warning('请填写名称')
    return
  }
  if (!form.command.trim()) {
    message.warning('请填写启动命令')
    return
  }
  let env: any = {}
  try {
    env = JSON.parse(form.env_text || '{}')
  } catch {
    message.error('env 不是合法 JSON')
    return
  }
  const payload = {
    description: form.description,
    version: form.version,
    enabled: form.enabled,
    shared: form.shared,
    share_with: form.share_with,
    command: form.command.trim(),
    args: form.args_text.split('\n').map((s) => s.trim()).filter(Boolean),
    env,
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.updateMcp(editing.value, payload)
      message.success('已更新')
    } else {
      await api.createMcp({ name: form.name.trim(), ...payload })
      message.success('已创建')
    }
    showCreate.value = false
    await load()
    await api.refreshMcp().catch(() => {})
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function remove(it: any) {
  try {
    await api.deleteMcp(it.name)
    message.success('已删除')
    await load()
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function onToggle(p: { name: string; enabled: boolean }) {
  try {
    await api.toggleMcp(p.name, p.enabled)
    const it = servers.value.find((s) => s.name === p.name)
    if (it) it.enabled = p.enabled
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  }
}

async function connect(it: any) {
  try {
    const d = await api.connectMcp(it.name)
    message.success(d.connected ? `已连接（${d.tool_count} 个工具）` : '连接失败：' + (d.error || '未知'))
    await load()
  } catch (e: any) {
    message.error(e?.message || '连接失败')
  }
}

async function openDetail(it: any) {
  detail.value = it
  testResult.value = ''
  testArgs.value = '{}'
  testTool.value = ''
  showDetail.value = true
  try {
    const t = await api.listTools()
    toolNames.value = (t.tools || [])
      .filter((x: any) => x.name.startsWith(`mcp__${it.name}__`))
      .map((x: any) => x.name.replace(`mcp__${it.name}__`, ''))
    if (toolNames.value.length) testTool.value = toolNames.value[0]
  } catch {
    toolNames.value = []
  }
}

async function runTest() {
  if (!detail.value || !testTool.value) return
  let args: any = {}
  try {
    args = JSON.parse(testArgs.value || '{}')
  } catch {
    message.error('参数不是合法 JSON')
    return
  }
  testing.value = true
  testResult.value = '调用中...'
  try {
    const d = await api.callMcp(detail.value.name, testTool.value, args)
    testResult.value = d.result ?? JSON.stringify(d)
  } catch (e: any) {
    testResult.value = '错误：' + (e?.message || e)
  } finally {
    testing.value = false
  }
}

onMounted(load)
watch(actAs, () => load())
</script>

<template>
  <div class="module-view">
    <header class="page-header">
      <div>
        <div class="header-title">MCP 服务器</div>
        <div class="header-sub">MCP（Model Context Protocol）服务器可对外提供工具。配置并连接后，其工具会注册为 mcp__&lt;server&gt;__&lt;tool&gt;，供 Agent 在对话中调用。</div>
      </div>
      <NButton type="primary" size="small" @click="openCreate">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </template>
        新建服务器
      </NButton>
    </header>

    <div class="module-content">
      <ModuleList
        title="MCP 服务器"
        :items="servers"
        :loading="loading"
        :editable="true"
        :show-status="true"
        empty-text="暂无 MCP 服务器，点击右上角「新建服务器」"
        search-placeholder="搜索 MCP 服务器..."
        @toggle="onToggle"
        @refresh="load"
        @edit="openEdit"
        @delete="remove"
      />
      <div class="row-actions" v-if="servers.length">
        <NButton v-for="s in servers" :key="s.name" size="tiny" @click="openDetail(s)">详情/测试：{{ s.name }}</NButton>
      </div>
    </div>

    <!-- 新建 / 编辑 -->
    <NModal v-model:show="showCreate" :title="editing ? '编辑 MCP 服务器' : '新建 MCP 服务器'" preset="card" style="width: 640px; max-width: 92vw;">
      <NForm>
        <NFormItem label="名称">
          <NInput v-model:value="form.name" :disabled="!!editing" placeholder="目录名（英文/数字/.-_）" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="form.description" placeholder="一句话描述" />
        </NFormItem>
        <NFormItem label="版本">
          <NInput v-model:value="form.version" placeholder="1.0.0" />
        </NFormItem>
        <NFormItem label="启用并连接">
          <NSwitch v-model:value="form.enabled" />
        </NFormItem>
        <NFormItem label="共享范围">
          <ShareScopeSelector v-model:shared="form.shared" v-model:share-with="form.share_with" />
        </NFormItem>
        <NFormItem label="启动命令">
          <NInput v-model:value="form.command" placeholder="如 python / npx / node" />
        </NFormItem>
        <NFormItem label="参数（每行一个）">
          <NInput v-model:value="form.args_text" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" placeholder="D:/path/server.py" />
        </NFormItem>
        <NFormItem label="环境变量（JSON）">
          <NInput v-model:value="form.env_text" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" placeholder='{"KEY":"VALUE"}' />
        </NFormItem>
      </NForm>
      <template #footer>
        <div class="modal-footer">
          <NButton @click="showCreate = false">取消</NButton>
          <NButton type="primary" :loading="submitting" @click="submit">保存</NButton>
        </div>
      </template>
    </NModal>

    <!-- 详情 / 测试 -->
    <NModal v-model:show="showDetail" :title="`MCP 详情：${detail?.name || ''}`" preset="card" style="width: 640px; max-width: 92vw;">
      <div v-if="detail" class="detail">
        <div class="status-line">
          <span>状态：</span>
          <b :class="detail.connected ? 'ok' : 'bad'">{{ detail.connected ? '已连接' : '未连接' }}</b>
          <span class="muted">（{{ detail.tool_count || 0 }} 个工具）</span>
        </div>
        <p v-if="detail.error" class="err">连接异常：{{ detail.error }}</p>
        <p class="muted">命令：{{ detail.command }} {{ (detail.args || []).join(' ') }}</p>

        <NButton size="small" @click="connect(detail)">重新连接</NButton>

        <div class="test-box" v-if="toolNames.length">
          <h4>工具测试</h4>
          <NSelect v-model:value="testTool" :options="toolNames.map((n) => ({ label: n, value: n }))" />
          <NInput v-model:value="testArgs" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" placeholder='{"key":"value"}' style="margin-top:8px" />
          <NButton size="small" type="primary" :loading="testing" @click="runTest" style="margin-top:8px">调用</NButton>
          <pre v-if="testResult" class="result">{{ testResult }}</pre>
        </div>
        <p v-else class="muted">该服务器暂无可用工具（请先连接）。</p>
      </div>
    </NModal>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.module-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.module-content { flex: 1; overflow-y: auto; padding-bottom: 16px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; }
.row-actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 20px 8px; }

.detail { font-size: 13px; color: $text-primary; }
.status-line { margin-bottom: 6px; }
.status-line .ok { color: #18a058; }
.status-line .bad { color: #d03050; }
.muted { color: $text-muted; font-size: 12px; }
.err { color: #d03050; font-size: 12px; }
.share-hint { font-size: 12px; color: $text-muted; margin-left: 10px; }
.test-box { margin-top: 14px; border-top: 1px solid $border-color; padding-top: 12px; }
.test-box h4 { margin: 0 0 8px; font-size: 13px; }
.result { background: $code-bg; padding: 10px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; max-height: 240px; overflow: auto; }
</style>
