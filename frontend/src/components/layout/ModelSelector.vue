<script setup lang="ts">
import { computed } from 'vue'
import { NSelect } from 'naive-ui'
import { useAppStore } from '@/stores/app'

const app = useAppStore()

const value = computed({
  get: () => app.selectedModel,
  set: (v: string) => app.selectModel(v),
})
const options = computed(() =>
  app.modelOptions.map((m) => ({ label: m.label, value: m.value })),
)
</script>

<template>
  <div class="model-selector">
    <NSelect
      :value="value"
      :options="options"
      size="small"
      placeholder="选择模型"
      :disabled="options.length === 0"
      @update:value="value = $event"
    />
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.model-selector {
  padding: 4px 12px 0;
}
</style>
