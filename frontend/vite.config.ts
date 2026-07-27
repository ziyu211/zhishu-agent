import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// 生产构建产物直接输出到后端静态目录，由 FastAPI 同源托管（彻底去掉 Node BFF）
export default defineConfig({
  plugins: [vue()],
  base: './',
  resolve: {
    alias: { '@': resolve(__dirname, 'src') }
  },
  server: {
    port: 5173,
    proxy: {
      // 开发期将 API 代理到本地 Python 后端
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: true }
    }
  },
  preview: {
    // 纯静态预览（不含后端 API）。完整应用请直接运行 FastAPI 后端，
    // 后端会同源托管前端与 /api，无需代理。
    port: 4173,
    outDir: '../backend/zhishu/static'
  },
  build: {
    outDir: '../backend/zhishu/static',
    // 构建前自动清空输出目录，避免旧 hash 文件堆积导致浏览器加载到旧版本
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500
  }
})
