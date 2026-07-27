<template>
  <div class="app-layout">
    <Sidebar :open="sidebarOpen" @navigate="sidebarOpen = false" />
    <div
      v-if="sidebarOpen"
      class="mobile-backdrop"
      @click="sidebarOpen = false"
    />
    <main class="main-area">
      <button class="hamburger-btn" @click="sidebarOpen = true" aria-label="菜单">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
      </button>
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import Sidebar from './Sidebar.vue'

const sidebarOpen = ref(false)
</script>

<style scoped lang="scss">
.app-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}
.main-area {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
