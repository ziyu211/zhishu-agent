/**
 * 知识库 API（文档列表 / 上传 / 读取 / 检索 / 统计）。
 */
import { request } from './http'
import type {
  DocumentItem,
  DocumentDetail,
  KnowledgeStats,
  KnowledgeSearchResp,
  UploadFileResp,
  ReparseResp,
} from './types'

export const ingestText = (text: string, doc_id?: string, title?: string) =>
  request<any>('/api/v1/knowledge/ingest', { method: 'POST', body: { text, doc_id, title } })
export const uploadFile = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request<UploadFileResp>('/api/v1/knowledge/upload', { method: 'POST', formData: fd })
}
export const listDocuments = (scope: 'mine' | 'all' = 'mine', q?: string) =>
  request<DocumentItem[]>(
    `/api/v1/knowledge/documents?scope=${scope}${q ? `&q=${encodeURIComponent(q)}` : ''}`,
  )
export const getDocument = (doc_id: string) =>
  request<DocumentDetail>(`/api/v1/knowledge/documents/${encodeURIComponent(doc_id)}`)
export const deleteDocument = (doc_id: string) =>
  request<any>(`/api/v1/knowledge/documents/${encodeURIComponent(doc_id)}`, { method: 'DELETE' })
export const reparseDocument = (doc_id: string) =>
  request<ReparseResp>(`/api/v1/knowledge/documents/${encodeURIComponent(doc_id)}/reparse`, {
    method: 'POST',
  })
export const searchKnowledge = (q: string, top_k = 5) =>
  request<KnowledgeSearchResp>(
    `/api/v1/knowledge/search?q=${encodeURIComponent(q)}&top_k=${top_k}`,
  )
export const knowledgeStats = () => request<KnowledgeStats>('/api/v1/knowledge/stats')
export interface KgNode {
  name: string
  freq: number
  doc_count: number
  docs?: string[]
}
export interface KgEdge {
  source: string
  target: string
  weight: number
  cross?: boolean
  docs?: string[]
}
export interface KgDocRef {
  doc_id: string
  title: string
}
export interface KgGraphResp {
  nodes: KgNode[]
  edges: KgEdge[]
  documents: KgDocRef[]
  doc_ids?: string[]
  stats: {
    nodes: number
    edges: number
    returned_nodes: number
    returned_edges: number
    cross_edges?: number
    docs?: number
  }
}
export const getKnowledgeGraph = (
  limit = 300,
  minWeight = 1,
  opts: { docIds?: string[]; crossDoc?: boolean } = {},
) => {
  const p = new URLSearchParams()
  p.set('limit', String(limit))
  p.set('min_weight', String(minWeight))
  p.set('include_docs', 'true')
  if (opts.crossDoc) p.set('cross_doc', 'true')
  for (const id of opts.docIds || []) p.append('doc_ids', id)
  return request<KgGraphResp>(`/api/v1/knowledge/graph?${p.toString()}`)
}

export const knowledgeApi = {
  ingestText,
  uploadFile,
  listDocuments,
  getDocument,
  deleteDocument,
  reparseDocument,
  searchKnowledge,
  knowledgeStats,
  getKnowledgeGraph,
}
