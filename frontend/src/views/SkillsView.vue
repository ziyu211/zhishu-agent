<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { NModal, NForm, NFormItem, NInput, NSwitch, NButton, NUpload, NDrawer, NDrawerContent, NAlert, NTag } from 'naive-ui'
import { api } from '@/api/client'
import { importSkills, exportSkills, exportSkill } from '@/api/skills'
import { actAs } from '@/api/actas'
import { useAppStore } from '@/stores/app'
import ModuleList from '@/components/modules/ModuleList.vue'
import ShareScopeSelector from '@/components/modules/ShareScopeSelector.vue'

const message = useMessage()
const app = useAppStore()
const loading = ref(false)
const skills = ref<any[]>([])

const showModal = ref(false)
const submitting = ref(false)
const editing = ref<string | null>(null)
const form = reactive<{ name: string; description: string; version: string; content: string; enabled: boolean; shared: boolean; share_with: string[] }>({
  name: '',
  description: '',
  version: '1.0.0',
  content: '',
  enabled: true,
  shared: false,
  share_with: [],
})

/** 本条目当前用户是否可编辑：需 modules:write；admin 恒可；owner 为自己可；历史无主条目仅 admin 可 */
function markEditable(items: any[]) {
  const me = (app.user as any)?.user || ''
  const admin = app.isAdmin
  const canWrite = app.can('modules:write')
  for (const it of items) it._editable = canWrite && (admin || (!!it.owner && it.owner === me))
  return items
}

// 导入相关
const showImport = ref(false)
const importing = ref(false)
const importFile = ref<File | null>(null)
const importResult = ref<any>(null)

async function load() {
  loading.value = true
  try {
    const d = await api.listSkills()
    skills.value = markEditable(d.skills || [])
  } catch (e: any) {
    message.error(e?.message || '加载技能失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', description: '', version: '1.0.0', content: '', enabled: true, shared: false, share_with: [] })
  showModal.value = true
}

async function openEdit(it: any) {
  try {
    const d = await api.getSkill(it.name)
    editing.value = it.name
    Object.assign(form, {
      name: d.name,
      description: d.description || '',
      version: d.version || '1.0.0',
      content: d.content || '',
      enabled: d.enabled !== false,
      shared: !!d.shared,
      share_with: d.share_with || [],
    })
    showModal.value = true
  } catch (e: any) {
    message.error(e?.message || '读取技能失败')
  }
}

async function submit() {
  if (!form.name.trim()) {
    message.warning('请填写技能名称')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.updateSkill(editing.value, {
        description: form.description,
        version: form.version,
        content: form.content,
        enabled: form.enabled,
        shared: form.shared,
        share_with: form.share_with,
      })
      message.success('已更新')
    } else {
      await api.createSkill({
        name: form.name.trim(),
        description: form.description,
        version: form.version,
        content: form.content,
        enabled: form.enabled,
        shared: form.shared,
        share_with: form.share_with,
      })
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
    await api.deleteSkill(it.name)
    message.success('已删除')
    await load()
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function onToggle(p: { name: string; enabled: boolean }) {
  try {
    await api.toggleSkill(p.name, p.enabled)
    const it = skills.value.find((s) => s.name === p.name)
    if (it) it.enabled = p.enabled
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  }
}

// ── 导入 ──
function openImport() {
  importFile.value = null
  importResult.value = null
  showImport.value = true
}
function onUploadChange(options: any) {
  const f = options?.file?.file as File | undefined
  if (f) importFile.value = f
}
async function doImport() {
  if (!importFile.value) {
    message.warning('请先选择要导入的压缩包（.zip / .tgz）')
    return
  }
  importing.value = true
  importResult.value = null
  try {
    const res = await importSkills(importFile.value)
    importResult.value = res
    if (res?.imported?.length) {
      message.success(`成功导入 ${res.imported.length} 个技能`)
      await load()
    } else {
      message.warning('未导入任何技能，请检查压缩包格式')
    }
  } catch (e: any) {
    message.error(e?.message || '导入失败')
  } finally {
    importing.value = false
  }
}

// ── 导出 ──
async function doExportAll() {
  try {
    await exportSkills()
    message.success('已导出全部技能（zip）')
  } catch (e: any) {
    message.error(e?.message || '导出失败')
  }
}
async function doExportOne(it: any) {
  try {
    await exportSkill(it.name)
    message.success(`已导出技能：${it.name}`)
  } catch (e: any) {
    message.error(e?.message || '导出失败')
  }
}

onMounted(load)
watch(actAs, () => load())
</script>

<template>
  <div class="module-view">
    <header class="page-header">
      <div>
        <div class="header-title">技能</div>
        <div class="header-sub">技能是一段注入 Agent 系统提示的指令（Markdown）。启用后，Agent 在每次对话都会参考这些指令。</div>
        <NTag v-if="!app.can('modules:write')" size="small" type="default" :bordered="false" style="margin-top:6px">只读模式</NTag>
      </div>
      <div class="header-actions">
        <NButton size="small" @click="doExportAll">
          <template #icon>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
            </svg>
          </template>
          导出全部
        </NButton>
        <NButton v-if="app.can('modules:write')" size="small" @click="openImport">
          <template #icon>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
            </svg>
          </template>
          导入
        </NButton>
        <NButton v-if="app.can('modules:write')" type="primary" size="small" @click="openCreate">
          <template #icon>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </template>
          新建技能
        </NButton>
      </div>
    </header>

    <div class="module-content">
      <ModuleList
        title="技能"
        :items="skills"
        :loading="loading"
        :editable="true"
        :exportable="true"
        empty-text="暂无技能，点击右上角「新建技能」"
        search-placeholder="搜索技能..."
        @toggle="onToggle"
        @refresh="load"
        @edit="openEdit"
        @delete="remove"
        @export="doExportOne"
      />
    </div>

    <NModal v-model:show="showModal" :title="editing ? '编辑技能' : '新建技能'" preset="card" style="width: 640px; max-width: 92vw;">
      <NForm>
        <NFormItem label="名称">
          <NInput v-model:value="form.name" :disabled="!!editing" placeholder="技能目录名（英文/数字/.-_）" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="form.description" placeholder="一句话描述这个技能的用途" />
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
        <NFormItem label="指令内容（注入 Agent）">
          <NInput
            v-model:value="form.content"
            type="textarea"
            :autosize="{ minRows: 6, maxRows: 18 }"
            placeholder="例如：当用户询问天气时，先确认城市，再调用工具查询。"
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

    <!-- 导入外部技能 -->
    <NDrawer v-model:show="showImport" :width="420" placement="right">
      <NDrawerContent title="导入技能" closable>
        <NAlert type="info" :show-icon="true" style="margin-bottom: 16px">
          支持从 <b>Hermes</b>、<b>OpenClaw</b> 等智能体，或智枢原生压缩包（.zip / .tgz）导入。
          系统会自动识别 <code>SKILL.md</code> / <code>module.json</code> / 通用 Markdown 格式并转换为智枢技能。
        </NAlert>

        <NUpload
          accept=".zip,.tgz,.tar.gz"
          :max="1"
          :default-upload="false"
          @change="onUploadChange"
        >
          <NButton>选择压缩包（.zip / .tgz）</NButton>
        </NUpload>

        <div v-if="importFile" class="import-file">
          已选择：<code>{{ importFile.name }}</code>
        </div>

        <div class="modal-footer" style="margin-top: 20px">
          <NButton @click="showImport = false">关闭</NButton>
          <NButton type="primary" :loading="importing" :disabled="!importFile" @click="doImport">开始导入</NButton>
        </div>

        <NAlert
          v-if="importResult"
          :type="importResult.imported?.length ? 'success' : 'warning'"
          :show-icon="true"
          style="margin-top: 18px"
        >
          <template #header>导入结果</template>
          <div v-if="importResult.detected_format?.length" class="ri">
            识别格式：{{ importResult.detected_format.join('、') }}
          </div>
          <div class="ri">成功：{{ (importResult.imported || []).length }} 个</div>
          <ul v-if="importResult.imported?.length" class="ri-list">
            <li v-for="s in importResult.imported" :key="s.name">{{ s.name }}<span v-if="s.description"> — {{ s.description }}</span></li>
          </ul>
          <div v-if="importResult.errors?.length" class="ri-err">失败：{{ importResult.errors.length }} 个</div>
          <ul v-if="importResult.errors?.length" class="ri-list ri-err">
            <li v-for="(e, i) in importResult.errors" :key="i">{{ e.name || '?' }}：{{ e.error }}</li>
          </ul>
        </NAlert>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.module-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.module-content { flex: 1; overflow-y: auto; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; }
.header-actions { display: flex; gap: 8px; flex-shrink: 0; }
.import-file { margin-top: 12px; font-size: 12px; color: $text-muted; }
.import-file code { font-family: $font-code; background: $code-bg; padding: 2px 6px; border-radius: 4px; }
.ri { font-size: 13px; }
.ri-list { margin: 6px 0 0; padding-left: 18px; font-size: 12px; color: $text-secondary; }
.ri-err { color: #d03050; }
.share-hint { font-size: 12px; color: $text-muted; margin-left: 10px; }
</style>
