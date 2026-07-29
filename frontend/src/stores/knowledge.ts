/**
 * 知识库 store。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listDocuments,
  getDocument,
  deleteDocument as apiDelete,
  uploadFile,
  knowledgeStats,
} from '@/api/knowledge'
import { toast } from '@/api/http'
import type { DocumentItem, DocumentDetail, KnowledgeStats, UploadFileResp } from '@/api/types'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const documents = ref<DocumentItem[]>([])
  const loading = ref(false)
  const stats = ref<KnowledgeStats | null>(null)

  async function load(scope: 'mine' | 'all' = 'mine') {
    loading.value = true
    try {
      documents.value = (await listDocuments(scope)).documents || []
    } catch (e: any) {
      toast('error', e?.message || '加载文档失败')
    } finally {
      loading.value = false
    }
  }
  async function loadStats() {
    try {
      stats.value = await knowledgeStats()
    } catch {
      /* noop */
    }
  }
  async function upload(file: File): Promise<UploadFileResp> {
    const r = await uploadFile(file)
    await load()
    await loadStats()
    return r
  }
  async function remove(doc_id: string) {
    await apiDelete(doc_id)
    await load()
    await loadStats()
  }
  async function open(doc_id: string): Promise<DocumentDetail | null> {
    try {
      return await getDocument(doc_id)
    } catch (e: any) {
      toast('error', e?.message || '读取文档失败')
      return null
    }
  }

  return { documents, loading, stats, load, loadStats, upload, remove, open }
})
