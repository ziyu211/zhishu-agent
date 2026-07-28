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

// —— 深色星空参考图配色 ——
const BG_DARK = '#0b1120' // 接近纯黑的深蓝
const PANEL_DARK = '#111827'
const BORDER_DARK = '#1f2937'
const TEXT_LIGHT = '#e2e8f0'
const TEXT_MUTED = '#94a3b8'
const CROSS_COLOR = '#f59e0b'
const HUB_COLOR = '#2563eb' // 中央大蓝核

// 40+ 独立彩色，按节点名哈希取，保证相邻节点颜色分散
const NODE_PALETTE = [
  '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16', '#22c55e',
  '#10b981', '#14b8a6', '#06b6d4', '#0ea5e9', '#3b82f6', '#6366f1',
  '#8b5cf6', '#a855f7', '#d946ef', '#ec4899', '#f43f5e', '#fb7185',
  '#fdba74', '#fcd34d', '#bef264', '#86efac', '#67e8f9', '#93c5fd',
  '#c4b5fd', '#f0abfc', '#fda4af', '#fca5a5', '#fed7aa', '#fde047',
  '#d9f99d', '#bbf7d0', '#99f6e4', '#a5f3fc', '#bfdbfe', '#c7d2fe',
  '#ddd6fe', '#f5d0fe', '#fbcfe8', '#fecdd3',
]

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

// 把字符串哈希成 0..m-1 的索引
function hashCode(str: string): number {
  let h = 0
  for (let i = 0; i < str.length; i++) h = (h << 5) - h + str.charCodeAt(i)
  return Math.abs(h)
}

function nodeColor(name: string, isHub: boolean): string {
  if (isHub) return HUB_COLOR
  return NODE_PALETTE[hashCode(name) % NODE_PALETTE.length]
}

function calcSize(freq: number, isHub: boolean): number {
  if (isHub) return Math.min(120, 55 + Math.sqrt(freq) * 4)
  return Math.min(70, Math.max(18, 14 + Math.sqrt(freq) * 5))
}

function buildOption() {
  // 中央大蓝核 = 频次最高节点
  const hubName = graph.value.nodes.length
    ? [...graph.value.nodes].sort((a, b) => b.freq - a.freq)[0].name
    : ''

  const nodes = graph.value.nodes.map((n) => {
    const isHub = n.name === hubName
    const color = nodeColor(n.name, isHub)
    const size = calcSize(n.freq, isHub)
    return {
      name: n.name,
      value: n.freq,
      symbolSize: size,
      // 中央节点固定居中，形成放射状
      ...(isHub ? { fixed: true, x: 0, y: 0 } : {}),
      itemStyle: {
        color,
        borderColor: isHub ? '#93c5fd' : (n.docs?.length || 0) > 1 ? '#ffffff' : 'rgba(255,255,255,0.18)',
        borderWidth: isHub ? 4 : (n.docs?.length || 0) > 1 ? 2 : 1,
        shadowBlur: isHub ? 28 : 10,
        shadowColor: isHub ? 'rgba(37,99,235,0.55)' : `${color}66`,
      },
      label: {
        show: true,
        position: isHub ? 'inside' : 'outside',
        distance: isHub ? 0 : 6,
        fontSize: isHub ? 16 : Math.max(10, Math.min(13, 9 + size / 10)),
        color: '#f8fafc',
        fontWeight: isHub ? 700 : 500,
        formatter: (p: any) => p.data.name,
        textBorderColor: 'rgba(0,0,0,0.75)',
        textBorderWidth: 2,
      },
      _doc: n.doc_count,
      _docs: n.docs || [],
    }
  })

  const links = graph.value.edges.map((e) => {
    const isCross = !!e.cross
    return {
      source: e.source,
      target: e.target,
      value: e.weight,
      _cross: isCross,
      _docs: e.docs || [],
      lineStyle: isCross
        ? { width: 1.2, opacity: 0.55, color: CROSS_COLOR, type: 'dashed' as const, curveness: 0.25 }
        : {
            width: Math.min(1 + e.weight * 0.25, 2.2),
            opacity: 0.18 + Math.min(e.weight / 18, 0.22),
            color: '#94a3b8',
          },
    }
  })

  return {
    backgroundColor: BG_DARK,
    tooltip: {
      backgroundColor: 'rgba(15,23,42,0.96)',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (p: any) => {
        if (p.dataType === 'node') {
          const src = docTitles(p.data._docs)
          const srcHtml = src.length
            ? `<br/>来源文档：${src.map((t) => `「${t}」`).join(' ')}`
            : ''
          return `<b style="color:${p.color}">${p.data.name}</b><br/>出现频次：${p.data.value}<br/>涉及文档：${p.data._doc} 篇${srcHtml}`
        }
        if (p.data._cross) {
          const src = docTitles(p.data._docs)
          return `${p.data.source} ⇢ ${p.data.target}<br/><span style="color:${CROSS_COLOR}">跨文档共现</span>：共同出现于 ${p.data.value} 篇文档${src.length ? `<br/>${src.map((t) => `「${t}」`).join(' ')}` : ''}`
        }
        const src = docTitles(p.data._docs)
        return `${p.data.source} — ${p.data.target}<br/>共现强度：${p.data.value}${src.length ? `<br/>来源文档：${src.map((t) => `「${t}」`).join(' ')}` : ''}`
      },
    },
    animationDuration: 1600,
    animationEasingUpdate: 'cubicOut',
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: {
          repulsion: 720,
          edgeLength: [80, 200],
          gravity: 0.08,
          friction: 0.14,
          layoutAnimation: true,
        },
        label: { show: true },
        edgeSymbol: ['none', 'none'],
        lineStyle: { curveness: 0.04 },
        emphasis: {
          focus: 'adjacency',
          scale: true,
          label: { show: true, fontSize: 14, fontWeight: 700 },
          lineStyle: { opacity: 0.9, width: 2.5 },
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
      <span class="kg-tip">彩色节点=关键词 · 灰线=共现 · 虚线=跨文档共现</span>
    </div>

    <div v-if="legendDocs.length" class="kg-legend">
      <span class="kg-legend-hint">来源文档：</span>
      <span
        v-for="d in legendDocs"
        :key="d.doc_id"
        class="kg-legend-item"
        :class="{ dim: docFilter.length && !docFilter.includes(d.doc_id) }"
      >
        {{ d.title }}
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
.kg-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0b1120;
  padding: 16px;
  border-radius: $radius-md;
}
.kg-toolbar {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding-bottom: 12px;
}
.kg-switch { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: $text-secondary; cursor: pointer; }
.kg-stat { font-size: 13px; color: $text-secondary; b { color: $text-primary; } .cross { color: #f59e0b; } }
.kg-tip { font-size: 11px; color: $text-muted; margin-left: auto; }
.kg-legend {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-bottom: 12px; font-size: 12px; color: #94a3b8;
}
.kg-legend-hint { color: #64748b; }
.kg-legend-item {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 8px;
  background: rgba(255,255,255,0.06);
  border-radius: 999px;
  &.dim { opacity: 0.35; }
}
.kg-empty { padding: 80px 0; }
.kg-canvas-wrap { position: relative; width: 100%; }
.kg-empty-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
.kg-chart {
  width: 100%;
  height: calc(100vh - 250px);
  min-height: 440px;
  background: #0b1120;
  border: 1px solid #1f2937;
  border-radius: $radius-md;
}
.kg-detail {
  margin-top: 12px;
  padding: 12px 14px;
  background: #111827;
  border: 1px solid #1f2937;
  border-radius: $radius-md;
  color: #e2e8f0;
}
.kg-detail-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.kg-detail-rel { margin-top: 8px; font-size: 12px; color: #94a3b8; }
.rel-tag { margin: 2px 4px 2px 0; }
.muted { color: #64748b; }
</style>
