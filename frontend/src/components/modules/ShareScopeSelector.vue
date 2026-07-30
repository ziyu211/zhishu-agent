<script setup lang="ts">
import { ref, watch } from 'vue'
import { NSpace, NRadioGroup, NRadioButton, NCheckboxGroup, NCheckbox } from 'naive-ui'

const props = defineProps<{
  shared: boolean
  shareWith: string[]
}>()
const emit = defineEmits<{
  (e: 'update:shared', v: boolean): void
  (e: 'update:shareWith', v: string[]): void
}>()

type Scope = 'private' | 'all' | 'roles'
const scope = ref<Scope>('private')

// 角色选项：admin 恒可见所有项，无需作为共享目标
const ROLE_OPTIONS = [
  { label: '操作员', value: 'operator' },
  { label: '普通用户', value: 'user' },
  { label: '访客', value: 'viewer' },
]

// 父组件打开「编辑」时会重新赋值 shared / share_with，需同步内部 scope
function syncFromProps() {
  scope.value = props.shared ? 'all' : (props.shareWith && props.shareWith.length ? 'roles' : 'private')
}
watch(() => [props.shared, props.shareWith], syncFromProps, { immediate: true })

// 用户切换共享范围 → 回写 shared / share_with（roles 模式保留已选角色）
watch(scope, (s) => {
  if (s === 'all') {
    emit('update:shared', true)
    emit('update:shareWith', [])
  } else if (s === 'private') {
    emit('update:shared', false)
    emit('update:shareWith', [])
  } else {
    emit('update:shared', false)
  }
})

function onRolesChange(vals: string[]) {
  emit('update:shareWith', vals)
}
</script>

<template>
  <div class="share-scope">
    <NRadioGroup v-model:value="scope" size="small">
      <NSpace>
        <NRadioButton value="private">私有（仅自己）</NRadioButton>
        <NRadioButton value="all">共享给所有人</NRadioButton>
        <NRadioButton value="roles">按角色共享</NRadioButton>
      </NSpace>
    </NRadioGroup>
    <div v-if="scope === 'roles'" class="role-box">
      <NCheckboxGroup :value="shareWith" @update:value="onRolesChange">
        <NSpace>
          <NCheckbox v-for="r in ROLE_OPTIONS" :key="r.value" :value="r.value">{{ r.label }}</NCheckbox>
        </NSpace>
      </NCheckboxGroup>
      <div class="role-hint">选中的角色成员可见 / 可使用（仅创建者可编辑）</div>
    </div>
    <div v-else-if="scope === 'all'" class="role-hint">所有登录用户可见 / 可使用（仅创建者可编辑）</div>
    <div v-else class="role-hint">仅你自己可见 / 可管理</div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.share-scope { display: flex; flex-direction: column; gap: 8px; }
.role-box {
  margin-top: 2px;
  padding: 10px 12px;
  background: rgba(var(--accent-primary-rgb), 0.04);
  border: 1px solid $border-color;
  border-radius: 6px;
}
.role-hint { font-size: 12px; color: $text-muted; }
</style>
