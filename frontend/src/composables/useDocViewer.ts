/**
 * 文档查看器（共享状态）。
 * 聊天窗口中上传/引用的文档卡片点击后，统一从这里打开：
 * 拉取后端文档详情（元数据 + 提取正文）并弹出查看器。
 */
import { reactive } from 'vue'
import { api } from '@/api/client'

interface DocViewerState {
  open: boolean
  loading: boolean
  error: string
  docId: string
  title: string
  doc: any
}

const state = reactive<DocViewerState>({
  open: false,
  loading: false,
  error: '',
  docId: '',
  title: '',
  doc: null,
})

export function useDocViewer() {
  async function openDoc(docId: string, title = '') {
    if (!docId) return
    state.docId = docId
    state.title = title || docId
    state.open = true
    state.loading = true
    state.error = ''
    state.doc = null
    try {
      const d = await api.getDocument(docId)
      state.doc = d
    } catch (e: any) {
      state.error = e?.message || '加载文档失败'
    } finally {
      state.loading = false
    }
  }

  function close() {
    state.open = false
  }

  return { state, openDoc, close }
}
