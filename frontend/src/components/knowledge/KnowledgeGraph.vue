<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { NButton, NEmpty, NSpin, NSelect, NSwitch, useMessage, NTag } from 'naive-ui'
import { getKnowledgeGraph, type KgGraphResp, type KgDocRef, type KgEdge } from '@/api/knowledge'

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
// 关联强度：默认只显示强关联，过滤单次共现的噪音边
const minW = ref(2)
const minWOpts = [
  { label: '全部关联', value: 1 },
  { label: '强关联 (≥2)', value: 2 },
  { label: '极强关联 (≥3)', value: 3 },
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

// —— 配色（清爽莫兰迪色板，按文档顺序取色）——
const DOC_PALETTE = ['#6ea8ff', '#5fd0a8', '#ff8a9b', '#c89bff', '#5cc8e0', '#ffb15b', '#a8e05f', '#f48cc0', '#8f9bff', '#5bc8b0']
const MULTI_DOC_COLOR = '#ffd166'
const CROSS_COLOR = '#f4a261'
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

// 常驻标签：仅高频重要节点（其余靠 hover/点击显示），避免文字堆叠
const labelTopSet = computed<Set<string>>(() => {
  const arr = [...graph.value.nodes].sort((a, b) => b.freq - a.freq)
  const topN = Math.min(36, Math.max(8, Math.floor(arr.length * 0.4)))
  return new Set(arr.slice(0, topN).map((n) => n.name))
})

function docTitles(ids?: string[]): string[] {
  return (ids || []).map((id) => docTitleMap.value[id] || id)
}
function nodeColor(docs?: string[]): string {
  if (!docs || docs.length === 0) return FALLBACK
  if (docs.length === 1) return docColorMap.value[docs[0]] || FALLBACK
  return MULTI_DOC_COLOR
}
// 边着色：同一文档内的关联用该文档色（成色簇），跨文档用琥珀，多文档共现取首色
function edgeColor(e: KgEdge): string {
  if (e.cross) return CROSS_COLOR
  const ds = e.docs || []
  if (ds.length === 1) return docColorMap.value[ds[0]] || FALLBACK
  return FALLBACK
}

function buildOption() {
  const total = graph.value.nodes.length || 1
  // 斥力自适应：节点越多越散，避免挤成一团
  const repulsion = Math.min(720, Math.max(170, Math.round(2600 / Math.sqrt(total))))
  const edgeLen: [number, number] = [50, Math.min(200, 45 + total * 1.1)]
  const top = labelTopSet.value

  const nodes = graph.value.nodes.map((n) => ({
    name: n.name,
    value: n.freq,
    symbolSize: 9 + Math.sqrt(n.freq) * 4.2,
    itemStyle: {
      color: nodeColor(n.docs),
      borderColor: (n.docs?.length || 0) > 1 ? 'rgba(255,255,255,0.9)' : 'transparent',
      borderWidth: (n.docs?.length || 0) > 1 ? 1.5 : 0,
      shadowBlur: 6,
      shadowColor: 'rgba(0,0,0,0.35)',
    },
    label: { show: top.has(n.name) },
    _doc: n.doc_count,
    _docs: n.docs || [],
  }))
  const links = graph.value.edges.map((e) => {
    const w = e.weight
    const isCross = !!e.cross
    return {
      source: e.source,
      target: e.target,
      value: w,
      _cross: isCross,
      _docs: e.docs || [],
      lineStyle: isCross
        ? { width: 1, opacity: 0.5, color: CROSS_COLOR, type: 'dashed' as const, curveness: 0.22 }
        : {
            width: Math.min(0.6 + w * 0.9, 7),
            opacity: 0.14 + Math.min(w / 10, 0.5),
            color: edgeColor(e),
          },
    }
  })
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
          return `${p.data.source} ⇢ ${p.data.target}<br/><span style="color:${CROSS_COLOR}">跨文档共现</span>：共同出现于 ${p.data.value} 篇文档${src.length ? `<br/>${src.map((t) => `「${t}」`).join(' ')}` : ''}`
        }
        const src = docTitles(p.data._docs)
        return `${p.data.source} — ${p.data.target}<br/>共现强度：${p.data.value}${src.length ? `<br/>来源文档：${src.map((t) => `「${t}」`).join(' ')}` : ''}`
      },
    },
    animationDuration: 1000,
    animationEasingUpdate: 'quinticInOut',
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion, edgeLength: edgeLen, gravity: 0.05, friction: 0.18, layoutAnimation: true },
        label: {
          show: false,
          fontSize: 11,
          color: '#e8eef7',
          position: 'right',
          textBorderColor: 'rgba(7,11,18,0.92)',
          textBorderWidth: 3,
          formatter: (p: any) => p.data.name,
        },
        edgeSymbol: ['none', 'none'],
        lineStyle: { curveness: 0.06 },
        emphasis: {
          focus: 'adjacency',
          scale: true,
          label: { show: true },
          lineStyle: { width: 2.2, opacity: 0.85 },
        },
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
    const r = await getKnowledgeGraph(limit.value, minW.value, {
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
      <div class="kg-group">
        <NButton size="small" type="primary" :loading="loading" @click="load">刷新图谱</NButton>
        <NSelect v-model:value="limit" :options="limitOpts" size="small" style="width: 116px" @update:value="load" />
        <NSelect v-model:value="minW" :options="minWOpts" size="small" style="width: 132px" @update:value="load" />
      </div>
      <div class="kg-group">
        <NSelect
          v-model:value="docFilter"
          :options="docOptions"
          multiple
          clearable
          size="small"
          style="min-width: 200px; max-width: 380px"
          placeholder="全部文档（可多选筛选）"
          max-tag-count="responsive"
          @update:value="load"
        />
        <label class="kg-switch">
          <NSwitch v-model:value="crossDoc" size="small" @update:value="load" />
          <span>跨文档</span>
        </label>
      </div>
      <div class="kg-group kg-info">
        <span class="kg-stat">
          <b>{{ graph.stats.returned_nodes }}</b> 节点 / <b>{{ graph.stats.returned_edges }}</b> 关系
          <template v-if="crossDoc && graph.stats.cross_edges"> · <b class="cross">{{ graph.stats.cross_edges }}</b> 跨文档</template>
        </span>
        <span class="kg-tip">拖拽平移 · 滚轮缩放 · 悬停聚焦邻域</span>
      </div>
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
      <span class="kg-legend-item"><span class="kg-line"></span>同色 = 同文档内关联</span>
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
  display: flex; align-items: center; flex-wrap: wrap;
  gap: 10px; padding: 4px 2px 10px;
}
.kg-group {
  display: inline-flex; align-items: center; gap: 10px;
  padding-left: 12px; margin-left: 2px;
  border-left: 1px solid $border-color;
  &:first-child { border-left: none; padding-left: 0; margin-left: 0; }
}
.kg-info { margin-left: auto; }
.kg-switch { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: $text-secondary; cursor: pointer; }
.kg-stat { font-size: 13px; color: $text-secondary; b { color: $text-primary; } .cross { color: #f4a261; } }
.kg-tip { font-size: 11px; color: $text-muted; margin-left: 10px; }
.kg-legend {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 0 2px 10px; font-size: 12px; color: $text-secondary;
}
.kg-legend-item { display: inline-flex; align-items: center; gap: 5px; &.dim { opacity: 0.35; } }
.kg-canvas-wrap { position: relative; width: 100%; }
.kg-empty-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
.kg-chart {
  width: 100%;
  height: calc(100vh - 250px);
  min-height: 440px;
  background: radial-gradient(circle at 50% 38%, #131c2c 0%, #0d1420 100%);
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
.kg-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex: none; &.multi { background: #ffd166; box-shadow: 0 0 0 1.5px #fff inset; } }
.kg-line { width: 18px; height: 3px; border-radius: 2px; background: $text-secondary; display: inline-block; opacity: 0.6; }
.kg-detail-rel { margin-top: 8px; font-size: 12px; color: $text-secondary; }
.rel-tag { margin: 2px 4px 2px 0; }
.muted { color: $text-muted; }
</style>
