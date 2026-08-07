<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { NButton, NTag, useMessage, useDialog } from 'naive-ui'
import { api } from '@/api/client'
import { actAs } from '@/api/actas'
import { useAppStore } from '@/stores/app'
import ProviderCard from './ProviderCard.vue'
import ProviderFormModal from './ProviderFormModal.vue'

const message = useMessage()
const dialog = useDialog()
const app = useAppStore()
const canWrite = computed(() => app.can('models:write'))

const providers = ref<any[]>([])
const presets = ref<any[]>([])
const compatOptions = ref<any[]>([])
const defaultModel = ref('')
const loading = ref(false)

const showModal = ref(false)
const modalMode = ref<'add' | 'edit'>('add')
const editing = ref<any | null>(null)

const sortedProviders = computed(() => [...providers.value].sort((a, b) => (a.priority ?? 50) - (b.priority ?? 50)))

async function load() {
  loading.value = true
  try {
    const [p, pr] = await Promise.all([api.listProviders(), api.listPresets()])
    providers.value = p.providers || []
    defaultModel.value = p.default_model || ''
    presets.value = pr.presets || []
    compatOptions.value = pr.compat_options || []
  } catch (e: any) {
    message.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function isDefault(p: any): boolean {
  if (!defaultModel.value) return false
  const [dn] = defaultModel.value.split('/')
  return dn === p.provider
}

function openAdd() {
  modalMode.value = 'add'
  editing.value = null
  showModal.value = true
}
function openEdit(p: any) {
  modalMode.value = 'edit'
  editing.value = p
  showModal.value = true
}

async function handleSave(payload: any) {
  try {
    if (modalMode.value === 'edit' && editing.value) {
      await api.updateProvider(editing.value.provider, payload)
      message.success('已更新')
    } else {
      await api.addProvider(payload)
      message.success('已添加')
    }
    showModal.value = false
    await load()
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  }
}

async function toggleEnabled(p: any, enabled: boolean) {
  if (!canWrite.value) { message.warning('当前角色为只读，无权修改'); return }
  try {
    await api.updateProvider(p.provider, { enabled })
    p.enabled = enabled
    message.success(enabled ? '已启用' : '已停用')
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  }
}

function setDefault(p: any) {
  if (!canWrite.value) { message.warning('当前角色为只读，无权修改'); return }
  if (!p.models || !p.models.length) {
    message.warning('该 Provider 暂无模型')
    return
  }
  const m = `${p.provider}/${p.models[0]}`
  api.setDefaultModel(m)
    .then(() => { defaultModel.value = m; message.success('已设为默认') })
    .catch((e: any) => message.error(e?.message || '操作失败'))
}

function removeProvider(p: any) {
  if (!canWrite.value) { message.warning('当前角色为只读，无权修改'); return }
  dialog.warning({
    title: '删除 Provider',
    content: `确认删除「${p.label || p.provider}」？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await api.deleteProvider(p.provider)
        message.success('已删除')
        await load()
      } catch (e: any) {
        message.error(e?.message || '删除失败')
      }
    },
  })
}

onMounted(load)
watch(actAs, () => load())
</script>

<template>
  <div class="providers-panel">
    <div class="panel-head">
      <div>
        <div class="panel-count">{{ providers.length }} 个 Provider</div>
        <div class="panel-sub">已启用按优先级回退：{{ providers.filter((p) => p.enabled !== false).length }} 个</div>
      </div>
      <NButton v-if="canWrite" type="primary" size="small" :loading="loading" @click="openAdd">
        <template #icon><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></template>
        添加 Provider
      </NButton>
      <NTag v-else size="small" :bordered="false" type="warning">只读</NTag>
    </div>

    <div v-if="defaultModel" class="default-line">
      当前默认模型：<code>{{ defaultModel }}</code>
    </div>

    <div class="provider-grid">
      <ProviderCard
        v-for="p in sortedProviders"
        :key="p.provider"
        :provider="p"
        :is-default="isDefault(p)"
        :can-edit="canWrite"
        @toggle="(v: boolean) => toggleEnabled(p, v)"
        @edit="openEdit(p)"
        @delete="removeProvider(p)"
        @set-default="setDefault(p)"
      />
      <button v-if="canWrite" class="add-card" @click="openAdd">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        <span>添加 Provider</span>
      </button>
    </div>

    <ProviderFormModal
      v-model:show="showModal"
      :mode="modalMode"
      :presets="presets"
      :compat-options="compatOptions"
      :provider="editing"
      @save="handleSave"
    />
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.providers-panel { padding: 20px; }

.panel-head { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 12px; }
.panel-count { font-size: 15px; font-weight: 600; color: $text-primary; }
.panel-sub { font-size: 12px; color: $text-muted; margin-top: 2px; }

.default-line { font-size: 13px; color: $text-secondary; margin-bottom: 16px;
  code { font-family: $font-code; background: $code-bg; padding: 2px 6px; border-radius: 4px; color: $text-primary; } }

.provider-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }

.add-card {
  border: 1px dashed $border-color;
  border-radius: $radius-md;
  background: transparent;
  color: $text-muted;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 180px;
  cursor: pointer;
  transition: all $transition-fast;
  &:hover { border-color: $accent-muted; color: $text-secondary; background: rgba(var(--accent-primary-rgb), 0.03); }
  span { font-size: 13px; }
}
</style>
