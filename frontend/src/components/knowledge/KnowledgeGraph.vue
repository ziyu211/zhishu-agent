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

// —— 参考图风格配色：浅色背景上的高饱和主题色 ——
const DOC_PALETTE = [
  '#ef5350', // 红
  '#42a5f5', // 蓝
  '#ffa726', // 橙
  '#66bb6a', // 绿
  '#ffca28', // 黄
  '#ab47bc', // 紫
  '#26c6da', // 青
  '#ec407a', // 粉
  '#7e57c2', // 深紫
  '#26a69a', // teal
]
const MULTI_DOC_COLOR = '#5c6b7f' // 多文档桥接节点用深灰，稳重不抢戏
const CROSS_COLOR = '#f4a261'
const FALLBACK = '#9ca3af'
const BG_LIGHT = '#f8fafc'

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
// 根据背景色亮度决定节点内文字用白或深灰
function labelColor(bgHex: string): string {
  const hex = bgHex.replace('#', '')
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
  return luminance > 0.62 ? '#374151' : '#ffffff'
}
function calcSize(freq: number): number {
  return Math.min(90, Math.max(26, 22 + Math.sqrt(freq) * 8))
}

function buildOption() {
  const nodes = graph.value.nodes.map((n) => {
    const color = nodeColor(n.docs)
    const size = calcSize(n.freq)
    return {
      name: n.name,
      value: n.freq,
      symbolSize: size,
      itemStyle: {
        color,
        // 多文档节点用白描边强调桥接身份
        borderColor: (n.docs?.length || 0) > 1 ? '#ffffff' : 'rgba(0,0,0,0.08)',
        borderWidth: (n.docs?.length || 0) > 1 ? 2.5 : 1,
        shadowBlur: 8,
        shadowColor: 'rgba(0,0,0,0.12)',
      },
      label: {
        show: true,
        position: 'inside',
        fontSize: size >= 56 ? 12 : 10,
        color: labelColor(color),
        fontWeight: 500,
        formatter: (p: any) => p.data.name,
        textBorderColor: 'rgba(0,0,0,0.12)',
        textBorderWidth: 1,
      },
      _doc: n.doc_count,
      _docs: n.docs || [],
    }
  })
  const links = graph.value.edges.map((e) => {
    const isCross = !!e.cross
    const showLabel = !isCross && e.weight >= 3
    return {
      source: e.source,
      target: e.target,
      value: e.weight,
      _cross: isCross,
      _docs: e.docs || [],
      label: {
        show: showLabel,
        formatter: '{c}',
        fontSize: 10,
        color: '#6b7280',
        textBorderColor: 'rgba(248,250,252,0.85)',
        textBorderWidth: 2,
      },
      lineStyle: isCross
        ? { width: 1.5, opacity: 0.65, color: CROSS_COLOR, type: 'dashed' as const, curveness: 0.2 }
        : {
            width: Math.min(1 + e.weight * 0.35, 3.5),
            opacity: 0.3 + Math.min(e.weight / 14, 0.35),
            color: '#9ca3af',
          },
    }
  })
  return {
    backgroundColor: BG_LIGHT,
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
    animationDuration: 1400,
    animationEasingUpdate: 'cubicOut',
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion: 650, edgeLength: [110, 240], gravity: 0.04, friction: 0.16, layoutAnimation: true },
        label: { show: true },
        edgeSymbol: ['none', 'none'],
        lineStyle: { curveness: 0.05 },
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
      <span class="kg-tip">节点色=来源文档 · 灰线=共现 · 虚线=跨文档共现</span>
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
.kg-stat { font-size: 13px; color: $text-secondary; b { color: $text-primary; } .cross { color: #f4a261; } }
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
  background: #f8fafc;
  border: 1px solid #e2e8f0;
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
.kg-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex: none; &.multi { background: #5c6b7f; box-shadow: 0 0 0 1.5px #fff inset; } }
.kg-detail-rel { margin-top: 8px; font-size: 12px; color: $text-secondary; }
.rel-tag { margin: 2px 4px 2px 0; }
.muted { color: $text-muted; }
</style>
