<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTag, NSwitch, useMessage } from 'naive-ui'

const props = defineProps<{
  provider: any
  isDefault: boolean
  canEdit?: boolean
}>()
const emit = defineEmits<{
  (e: 'toggle', enabled: boolean): void
  (e: 'edit'): void
  (e: 'delete'): void
  (e: 'set-default'): void
}>()
const message = useMessage()

const editable = computed(() => props.canEdit !== false)
const enabled = computed(() => props.provider.enabled !== false)
const masked = computed(() => props.provider.api_key_masked || (props.provider.has_key ? '••••' : '未配置'))
</script>

<template>
  <div class="provider-card" :class="{ off: !enabled }">
    <div class="pc-head">
      <div class="pc-title">
        <span class="pc-name">{{ provider.label || provider.provider }}</span>
        <span v-if="isDefault" class="pc-default">默认</span>
      </div>
      <NSwitch :value="enabled" size="small" :disabled="!editable" @update:value="(v: boolean) => emit('toggle', v)" />
    </div>

    <div class="pc-type">
      <span class="pc-key">{{ provider.provider }}</span>
      <NTag v-if="provider.local" size="tiny" :bordered="false" class="pc-tag">本地</NTag>
      <NTag v-if="provider.builtin" size="tiny" :bordered="false" class="pc-tag">内置</NTag>
      <NTag v-if="provider.shared" size="tiny" type="info" :bordered="false">共享·全员</NTag>
      <NTag v-else-if="provider.share_with && provider.share_with.length" size="tiny" type="warning" :bordered="false">共享·按角色</NTag>
      <NTag v-else-if="provider.owner" size="tiny" :bordered="false" class="pc-tag">{{ provider.owner }}</NTag>
      <NTag v-else size="tiny" :bordered="false" class="pc-tag">公共</NTag>
    </div>

    <div class="pc-meta">
      <div class="pc-meta-row"><span class="pc-meta-label">端点</span><span class="pc-meta-val mono">{{ provider.base_url }}</span></div>
      <div class="pc-meta-row"><span class="pc-meta-label">密钥</span><span class="pc-meta-val mono">{{ masked }}</span></div>
      <div class="pc-meta-row"><span class="pc-meta-label">优先级</span><span class="pc-meta-val">{{ provider.priority }}</span></div>
    </div>

    <div class="pc-models">
      <span v-for="m in provider.models" :key="m" class="model-chip" :class="{ active: isDefault && m === provider.models[0] }">{{ m }}</span>
      <span v-if="!provider.models || !provider.models.length" class="model-chip empty">无模型</span>
    </div>

    <div class="pc-actions" v-if="editable">
      <NButton v-if="!isDefault" size="tiny" quaternary @click="emit('set-default')">设为默认</NButton>
      <NButton size="tiny" quaternary @click="emit('edit')">编辑</NButton>
      <NButton size="tiny" quaternary type="error" @click="emit('delete')">删除</NButton>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.provider-card {
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: box-shadow $transition-fast, border-color $transition-fast;
  &:hover { border-color: $accent-muted; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06); }
  &.off { opacity: 0.7; }

  .pc-head { display: flex; align-items: center; justify-content: space-between; }
  .pc-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .pc-name { font-size: 15px; font-weight: 600; color: $text-primary; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pc-default { font-size: 10px; padding: 1px 7px; border-radius: 8px; color: #fff; background: var(--brand); flex-shrink: 0; }

  .pc-type { display: flex; align-items: center; gap: 6px; }
  .pc-key { font-size: 12px; color: $text-muted; font-family: $font-code; }
  .pc-tag { color: $text-secondary; background: $bg-secondary; }

  .pc-meta { display: flex; flex-direction: column; gap: 3px; }
  .pc-meta-row { display: flex; gap: 8px; font-size: 12px; }
  .pc-meta-label { color: $text-muted; width: 32px; flex-shrink: 0; }
  .pc-meta-val { color: $text-secondary; word-break: break-all; }
  .mono { font-family: $font-code; }

  .pc-models { display: flex; flex-wrap: wrap; gap: 6px; min-height: 24px; }
  .model-chip {
    font-size: 12px; padding: 3px 9px; border-radius: 6px;
    background: $bg-secondary; color: $text-secondary; border: 1px solid $border-light;
    &.active { color: var(--brand); border-color: rgba(var(--brand-rgb), 0.4); }
    &.empty { font-style: italic; color: $text-muted; }
  }

  .pc-actions { display: flex; gap: 4px; border-top: 1px solid $border-light; padding-top: 10px; margin-top: 2px; }
}
</style>
