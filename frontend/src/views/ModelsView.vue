<script setup lang="ts">
import ProvidersPanel from '@/components/models/ProvidersPanel.vue'
</script>

<template>
  <div class="models-view">
    <header class="page-header">
      <div>
        <div class="header-title">模型</div>
        <div class="header-sub">已接入的国产 / 本地模型端点（按优先级回退）</div>
      </div>
    </header>
    <div class="models-content">
      <ProvidersPanel />
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.models-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.models-content { flex: 1; overflow-y: auto; }
</style>
