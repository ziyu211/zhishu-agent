<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { NButton, NEmpty, NSpin, NSelect, NSwitch, useMessage, NTag } from 'naive-ui'
import { getKnowledgeGraph, type KgGraphResp, type KgDocRef } from '@/api/knowledge'

const message = useMessage()
const chartEl = ref<HTMLElement | null>(null)
const loading = ref(false)
const graph = ref<KgGraphResp>({
  nodes: [],
  edges: [],
  documents: [],
  stats: { nodes: 0, edges: 0, returned_nodes: 0, returned_edges: 0 },
})
const limit = ref(200)
const limitOpts = [
  { label: '100 节点', value: 100 },
  { label: '200 节点', value: 200 },
  { label: '300 节点', value: 300 },
  { label: '500 节点', value: 500 },
]
// —— 文档筛选与跨文档开关 ——
const docFilter = ref<string[]>([]) // 空数组 = 全部文档
const crossDoc = ref(false)
const docOptions = computed(() =>
  graph.value.documents.map((d) => ({ label: d.title, value: d.doc_id })),
)
const selected = ref<{
  name: string
  freq: number
  doc_count: number
  docs: string[]
  neighbors: string[]
} | null>(null)

let chart: echarts.ECharts | null = null

// —— 配色 ——
// 文档专属色板（按文档在列表中的顺序取色）；多文档节点用琥珀色标识
const DOC_PALETTE = ['#5b9bff', '#5bd6a6', '#ff7a8a', '#c98bff', '#5bd0e6', '#ff9f5b', '#9be15b', '#f27bc0', '#8f9fff', '#5bc8b0']
const MULTI_DOC_COLOR = '#ffce5b'
const FALLBACK = '#7c8aa5'

const docColorMap = computed<Record<string, string>>(() => {
  const m: Record<string, string> = {}
  graph.value.documents.forEach((d, i) => {
    m[d.doc_id] = DOC_PALETTE[i % DOC_PALETTE.length]
  })
  return m
})
const docTitleMap = computed<Record<string, string>>(() => {
  const m: Record<string, string> = {}
  graph.value.documents.forEach((d) => (m[d.doc_id] = d.title))
  return m
})
// 图例只显示当前图谱实际涉及的文档
const legendDocs = computed<KgDocRef[]>(() => {
  const used = new Set<string>()
  for (const n of graph.value.nodes) for (const d of n.docs || []) used.add(d)
  return graph.value.documents.filter((d) => used.has(d.doc_id))
})

function docTitles(ids?: string[]): string[] {
  return (ids || []).map((id) => docTitleMap.value[id] || id)
}

function nodeColor(docs?: string[]): string {
  if (!docs || docs.length === 0) return FALLBACK
  if (docs.length === 1) return docColorMap.value[docs[0]] || FALLBACK
  return MULTI_DOC_COLOR
}

function buildOption() {
  const nodes = graph.value.nodes.map((n) => ({
    name: n.name,
    value: n.freq,
    symbolSize: 10 + Math.sqrt(n.freq) * 6,
    itemStyle: {
      color: nodeColor(n.docs),
      // 多文档节点加描边强调「桥接」身份
      borderColor: (n.docs?.length || 0) > 1 ? '#fff' : 'transparent',
      borderWidth: (n.docs?.length || 0) > 1 ? 1.5 : 0,
    },
    _doc: n.doc_count,
    _docs: n.docs || [],
  }))
  const links = graph.value.edges.map((e) => ({
    source: e.source,
    target: e.target,
    value: e.weight,
    _cross: !!e.cross,
    _docs: e.docs || [],
    lineStyle: e.cross
      ? { width: 1, opacity: 0.35, color: '#e8b45b', type: 'dashed' as const, curveness: 0.2 }
      : {
          width: Math.min(1 + e.weight, 8),
          opacity: 0.25 + Math.min(e.weight / 8, 0.55),
          color: '#7c8aa5',
        },
  }))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: 'rgba(20,26,38,0.95)',
      borderColor: '#2a3346',
      textStyle: { color: '#d6deea', fontSize: 12 },
      formatter: (p: any) => {
        if (p.dataType === 'node') {
          const src = docTitles(p.data._docs)
          const srcHtml = src.length
            ? `<br/>来源文档：${src.map((t) => `「${t}」`).join(' ')}`
            : ''
          return `<b>${p.data.name}</b><br/>出现频次：${p.data.value}<br/>涉及文档：${p.data._doc} 篇${srcHtml}`
        }
        if (p.data._cross) {
          const src = docTitles(p.data._docs)
          return `${p.data.source} ⇢ ${p.data.target}<br/><span style="color:#e8b45b">跨文档共现</span>：共同出现于 ${p.data.value} 篇文档${src.length ? `<br/>${src.map((t) => `「${t}」`).join(' ')}` : ''}`
        }
        const src = docTitles(p.data._docs)
        return `${p.data.source} ↔ ${p.data.target}<br/>共现强度：${p.data.value}${src.length ? `<br/>来源文档：${src.map((t) => `「${t}」`).join(' ')}` : ''}`
      },
    },
    animationDuration: 1200,
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion: 200, edgeLength: [50, 140], gravity: 0.08, friction: 0.15 },
        label: { show: true, fontSize: 12, color: '#d6deea', formatter: (p: any) => p.data.name },
        edgeSymbol: ['none', 'none'],
        lineStyle: { curveness: 0.08 },
        emphasis: { focus: 'adjacency', scale: true, label: { show: true } },
        data: nodes,
        links,
      },
    ],
  }
}

function render() {
  if (!chart) return
  if (graph.value.nodes.length === 0) {
    chart.clear()
    return
  }
  chart.setOption(buildOption(), true)
  chart.resize()
}

let ro: ResizeObserver | null = null

// 在容器可见且有正确尺寸后再初始化图表，避免其在隐藏容器里以 0 尺寸初始化
// （那样只会在左上角渲染出残缺的一小块）
async function ensureChart() {
  await nextTick()
  if (!chart && chartEl.value) {
    chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
    chart.on('click', onChartClick)
    window.addEventListener('resize', onResize)
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => chart?.resize())
      ro.observe(chartEl.value)
    }
  }
  requestAnimationFrame(() => chart?.resize())
}

async function load() {
  loading.value = true
  selected.value = null
  try {
    const r = await getKnowledgeGraph(limit.value, 1, {
      docIds: docFilter.value.length ? docFilter.value : undefined,
      crossDoc: crossDoc.value,
    })
    graph.value = r
    await ensureChart()
    render()
  } catch (e: any) {
    message.error(e?.message || '加载知识图谱失败')
  } finally {
    loading.value = false
  }
}

function onResize() {
  chart?.resize()
}

function onChartClick(params: any) {
  if (params.dataType !== 'node') return
  const name = params.data.name
  const nb = new Set<string>()
  for (const e of graph.value.edges) {
    if (e.source === name) nb.add(e.target)
    if (e.target === name) nb.add(e.source)
  }
  const node = graph.value.nodes.find((n) => n.name === name)
  selected.value = node
    ? {
        name: node.name,
        freq: node.freq,
        doc_count: node.doc_count,
        docs: node.docs || [],
        neighbors: [...nb],
      }
    : null
}

onMounted(() => {
  load()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  ro?.disconnect()
  ro = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="kg-wrap">
    <div class="kg-toolbar">
      <NButton size="small" type="primary" :loading="loading" @click="load">刷新图谱</NButton>
      <NSelect v-model:value="limit" :options="limitOpts" size="small" style="width: 130px" @update:value="load" />
      <NSelect
        v-model:value="docFilter"
        :options="docOptions"
        multiple
        clearable
        size="small"
        style="min-width: 220px; max-width: 420px"
        placeholder="全部文档（可多选筛选）"
        max-tag-count="responsive"
        @update:value="load"
      />
      <label class="kg-switch">
        <NSwitch v-model:value="crossDoc" size="small" @update:value="load" />
        <span>跨文档共现</span>
      </label>
      <span class="kg-stat">
        展示 <b>{{ graph.stats.returned_nodes }}</b> 节点 / <b>{{ graph.stats.returned_edges }}</b> 关系
        <template v-if="crossDoc && graph.stats.cross_edges"> + <b class="cross">{{ graph.stats.cross_edges }}</b> 跨文档</template>
        （库内共 {{ graph.stats.nodes }} 节点）
      </span>
      <span class="kg-tip">节点色=来源文档 · 白边=多文档 · 虚线=跨文档共现</span>
    </div>

    <div v-if="legendDocs.length" class="kg-legend">
      <span
        v-for="d in legendDocs"
        :key="d.doc_id"
        class="kg-legend-item"
        :class="{ dim: docFilter.length && !docFilter.includes(d.doc_id) }"
      >
        <span class="kg-dot" :style="{ background: docColorMap[d.doc_id] }"></span>{{ d.title }}
      </span>
      <span v-if="legendDocs.length > 1" class="kg-legend-item">
        <span class="kg-dot multi"></span>多文档共有
      </span>
    </div>

    <NSpin :show="loading">
      <div class="kg-canvas-wrap">
        <div ref="chartEl" class="kg-chart"></div>
        <div v-if="!loading && graph.nodes.length === 0" class="kg-empty-overlay">
          <NEmpty description="暂无图谱数据，请先在「知识库」上传或粘贴文档" />
        </div>
      </div>
    </NSpin>

    <div v-if="selected" class="kg-detail">
      <div class="kg-detail-head">
        <span class="kg-dot" :style="{ background: nodeColor(selected.docs) }"></span>
        <b>{{ selected.name }}</b>
        <NTag size="small" :bordered="false">频次 {{ selected.freq }}</NTag>
        <NTag size="small" :bordered="false" type="info">文档 {{ selected.doc_count }}</NTag>
        <NTag
          v-for="t in docTitles(selected.docs)"
          :key="t"
          size="small"
          :bordered="false"
          type="warning"
        >{{ t }}</NTag>
      </div>
      <div class="kg-detail-rel">
        关联实体：
        <span v-if="selected.neighbors.length === 0" class="muted">无</span>
        <NTag
          v-for="nb in selected.neighbors"
          :key="nb"
          size="small"
          :bordered="false"
          class="rel-tag"
        >{{ nb }}</NTag>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.kg-wrap { display: flex; flex-direction: column; height: 100%; }
.kg-toolbar {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 4px 2px 10px;
}
.kg-switch { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: $text-secondary; cursor: pointer; }
.kg-stat { font-size: 13px; color: $text-secondary; b { color: $text-primary; } .cross { color: #e8b45b; } }
.kg-tip { font-size: 11px; color: $text-muted; margin-left: auto; }
.kg-legend {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 0 2px 10px; font-size: 12px; color: $text-secondary;
}
.kg-legend-item { display: inline-flex; align-items: center; gap: 5px; &.dim { opacity: 0.35; } }
.kg-empty { padding: 80px 0; }
.kg-canvas-wrap { position: relative; width: 100%; }
.kg-empty-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
.kg-chart {
  width: 100%;
  height: calc(100vh - 250px);
  min-height: 440px;
  background: #0f1623;
  border: 1px solid $border-color;
  border-radius: $radius-md;
}
.kg-detail {
  margin-top: 12px;
  padding: 12px 14px;
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $radius-md;
}
.kg-detail-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.kg-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex: none; &.multi { background: #ffce5b; box-shadow: 0 0 0 1.5px #fff inset; } }
.kg-detail-rel { margin-top: 8px; font-size: 12px; color: $text-secondary; }
.rel-tag { margin: 2px 4px 2px 0; }
.muted { color: $text-muted; }
</style>
