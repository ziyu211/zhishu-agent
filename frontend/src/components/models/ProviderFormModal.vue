<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  NModal, NForm, NFormItem, NInput, NSelect, NSwitch, NInputNumber, NButton, useMessage,
} from 'naive-ui'
import { useModelsStore } from '@/stores/models'

const props = defineProps<{
  show: boolean
  mode: 'add' | 'edit'
  presets: any[]
  provider?: any | null
}>()
const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'save', payload: any): void
}>()
const message = useMessage()
const modelsStore = useModelsStore()

const presetKey = ref<string | null>(null)
const name = ref('')
const label = ref('')
const baseUrl = ref('')
const apiKey = ref('')
const model = ref('')            // 默认模型（单选，可手输）
const modelsSel = ref<string[]>([]) // 模型列表（多选，可手输）
const local = ref(false)
const priority = ref(50)
const enabled = ref(true)

// 探测模型得到的候选（与 hermes-web-ui 对齐：下拉可搜索、可手输）
const modelOptions = computed(() => props.presets
  .filter((p) => p.provider === presetKey.value)
  .flatMap((p) => (p.models || []))
  .concat(detectedModels.value)
  .filter((v, i, a) => a.indexOf(v) === i)
  .map((m) => ({ label: m, value: m })))
const detectedModels = ref<string[]>([])
const fetching = ref(false)

const presetOptions = computed(() => [
  { label: '自定义端点', value: '__custom__' },
  ...props.presets.map((p) => ({ label: `${p.label}（${p.provider}）`, value: p.provider })),
])

const selectedPreset = computed(() => props.presets.find((p) => p.provider === presetKey.value) || null)

function applyPreset() {
  const p = selectedPreset.value
  if (p) {
    label.value = p.label
    baseUrl.value = p.base_url
    local.value = !!p.local
    modelsSel.value = [...(p.models || [])]
    if (!name.value) name.value = p.provider
    if (!model.value && p.models?.[0]) model.value = p.models[0]
  }
}

// 探测模型：对齐 hermes-web-ui —— 调后端 /api/v1/models/fetch（服务端请求 Provider 的 /v1/models），
// 把返回的模型名填充进下拉，供「默认模型 / 模型列表」选择。
async function fetchModels() {
  const base = baseUrl.value.trim()
  if (!base) {
    message.warning('请先填写 Base URL')
    return
  }
  fetching.value = true
  try {
    const list = await modelsStore.fetchRemote(base, apiKey.value)
    detectedModels.value = list
    if (!list.length) {
      message.warning('未探测到模型（端点可能无 /models 或需鉴权）')
      return
    }
    // 自动填充：模型列表为空时全选探测结果；默认模型为空时取第一个
    if (modelsSel.value.length === 0) modelsSel.value = [...list]
    if (!model.value) model.value = list[0]
    message.success(`探测到 ${list.length} 个模型`)
  } catch (e: any) {
    message.error('探测失败：' + (e?.message || e))
  } finally {
    fetching.value = false
  }
}

watch(
  () => props.show,
  (v) => {
    if (!v) return
    detectedModels.value = []
    if (props.mode === 'edit' && props.provider) {
      const p = props.provider
      presetKey.value = p.provider && props.presets.some((x) => x.provider === p.provider) ? p.provider : '__custom__'
      name.value = p.provider
      label.value = p.label || ''
      baseUrl.value = p.base_url || ''
      apiKey.value = ''
      model.value = (p.models && p.models[0]) || ''
      modelsSel.value = [...(p.models || [])]
      local.value = !!p.local
      priority.value = p.priority ?? 50
      enabled.value = p.enabled !== false
    } else {
      presetKey.value = props.presets[0]?.provider || '__custom__'
      name.value = ''
      label.value = ''
      baseUrl.value = ''
      apiKey.value = ''
      model.value = ''
      modelsSel.value = []
      local.value = false
      priority.value = 50
      enabled.value = true
      applyPreset()
    }
  },
)

function close() { emit('update:show', false) }

function handleSave() {
  if (!baseUrl.value.trim()) {
    message.error('请填写 base_url')
    return
  }
  // 模型列表 = 多选 + 默认模型（确保默认模型始终在目录内）
  const models = [...new Set([...(modelsSel.value || []), model.value].filter(Boolean))]
  const payload: any = {
    base_url: baseUrl.value.trim(),
    api_key: apiKey.value,           // 仅非空时后端才覆盖；编辑留空则不改密钥
    models,
    local: local.value,
    priority: priority.value,
  }
  if (props.mode === 'edit') {
    payload.enabled = enabled.value
    // 编辑也允许更新模型目录（含默认模型）
    if (!apiKey.value.trim()) delete payload.api_key
  } else {
    if (presetKey.value && presetKey.value !== '__custom__') payload.provider_key = presetKey.value
    payload.name = name.value.trim() || undefined
    payload.label = label.value.trim() || undefined
    payload.model = model.value.trim()
  }
  emit('save', payload)
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="mode === 'edit' ? '编辑 Provider' : '添加 Provider'"
    style="width: 560px; max-width: calc(100vw - 32px)"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <NForm label-placement="top">
      <NFormItem v-if="mode === 'add'" label="预设">
        <NSelect v-model:value="presetKey" :options="presetOptions" @update:value="applyPreset" />
      </NFormItem>

      <div v-if="mode === 'add' && presetKey === '__custom__'" class="row2">
        <NFormItem label="名称">
          <NInput v-model:value="name" placeholder="如 my_company_llm" />
        </NFormItem>
        <NFormItem label="显示名">
          <NInput v-model:value="label" placeholder="如 公司自研模型" />
        </NFormItem>
      </div>

      <NFormItem label="Base URL">
        <NInput v-model:value="baseUrl" placeholder="https://.../v1" />
      </NFormItem>
      <NFormItem label="API Key">
        <NInput v-model:value="apiKey" type="password" show-password-on="click" placeholder="留空表示无需密钥（本地模型）" />
      </NFormItem>

      <NFormItem label="默认模型">
        <div class="model-row">
          <NSelect
            v-model:value="model"
            :options="modelOptions"
            filterable
            tag
            placeholder="先点「探测」拉取，或手动输入"
            style="flex: 1"
          />
          <NButton :loading="fetching" @click="fetchModels">探测模型</NButton>
        </div>
      </NFormItem>

      <NFormItem label="模型列表（可多选 / 可手输）">
        <NSelect
          v-model:value="modelsSel"
          :options="modelOptions"
          multiple
          filterable
          tag
          placeholder="先点「探测模型」拉取，或从预设继承，亦可手动追加"
          style="width: 100%"
        />
      </NFormItem>

      <div class="row2">
        <NFormItem label="优先级">
          <NInputNumber v-model:value="priority" :min="0" :max="100" style="width: 100%" />
        </NFormItem>
        <NFormItem label="本地模型">
          <div class="switch-cell">
            <NSwitch v-model:value="local" /><span>不出网</span>
          </div>
        </NFormItem>
      </div>
      <div class="form-foot" v-if="mode === 'edit'">
        <NSwitch v-model:value="enabled" /><span>启用</span>
      </div>
    </NForm>

    <template #footer>
      <div class="modal-footer">
        <NButton @click="close">取消</NButton>
        <NButton type="primary" @click="handleSave">保存</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.model-row { display: flex; gap: 8px; width: 100%; }
.switch-cell { display: flex; align-items: center; gap: 8px; height: 100%; font-size: 13px; color: $text-secondary; }
.form-foot { display: flex; align-items: center; gap: 8px; font-size: 13px; color: $text-secondary; margin-top: 4px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
