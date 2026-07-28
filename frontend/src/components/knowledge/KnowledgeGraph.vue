<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { NButton, NEmpty, NSpin, NSelect, useMessage, NTag } from 'naive-ui'
import { getKnowledgeGraph } from '@/api/knowledge'

const message = useMessage()
const chartEl = ref<HTMLElement | null>(null)
const loading = ref(false)
const graph = ref<{
  nodes: { name: string; freq: number; doc_count: number }[]
  edges: { source: string; target: string; weight: number }[]
  stats: { nodes: number; edges: number; returned_nodes: number; returned_edges: number }
}>({ nodes: [], edges: [], stats: { nodes: 0, edges: 0, returned_nodes: 0, returned_edges: 0 } })
const limit = ref(200)
const limitOpts = [
  { label: '100 节点', value: 100 },
  { label: '200 节点', value: 200 },
  { label: '300 节点', value: 300 },
  { label: '500 节点', value: 500 },
]
const selected = ref<{ name: string; freq: number; doc_count: number; neighbors: string[] } | null>(null)

let chart: echarts.ECharts | null = null

const PALETTE = ['#5b9bff', '#ff7a8a', '#ffce5b', '#5bd6a6', '#c98bff', '#5bd0e6', '#ff9f5b', '#9be15b']

function colorFor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
}

function buildOption() {
  const nodes = graph.value.nodes.map((n) => ({
    name: n.name,
    value: n.freq,
    symbolSize: 10 + Math.sqrt(n.freq) * 6,
    itemStyle: { color: colorFor(n.name) },
    _doc: n.doc_count,
  }))
  const links = graph.value.edges.map((e) => ({
    source: e.source,
    target: e.target,
    value: e.weight,
    lineStyle: {
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
          return `<b>${p.data.name}</b><br/>出现频次：${p.data.value}<br/>涉及文档：${p.data._doc} 篇`
        }
        return `${p.data.source} ↔ ${p.data.target}<br/>共现强度：${p.data.value}`
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
}

async function load() {
  loading.value = true
  selected.value = null
  try {
    const r = await getKnowledgeGraph(limit.value, 1)
    graph.value = r
    await nextTick()
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
    ? { name: node.name, freq: node.freq, doc_count: node.doc_count, neighbors: [...nb] }
    : null
}

onMounted(async () => {
  await nextTick()
  if (chartEl.value) {
    chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
    chart.on('click', onChartClick)
    window.addEventListener('resize', onResize)
  }
  load()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="kg-wrap">
    <div class="kg-toolbar">
      <NButton size="small" type="primary" :loading="loading" @click="load">刷新图谱</NButton>
      <NSelect v-model:value="limit" :options="limitOpts" size="small" style="width: 130px" @update:value="load" />
      <span class="kg-stat">
        展示 <b>{{ graph.stats.returned_nodes }}</b> 节点 / <b>{{ graph.stats.returned_edges }}</b> 关系
        （库内共 {{ graph.stats.nodes }} 节点）
      </span>
      <span class="kg-tip">节点大小=出现频次 · 连线粗细=共现强度 · 拖拽/滚轮缩放</span>
    </div>

    <NSpin :show="loading">
      <div v-if="!loading && graph.nodes.length === 0" class="kg-empty">
        <NEmpty description="暂无图谱数据，请先在「知识库」上传或粘贴文档" />
      </div>
      <div v-show="graph.nodes.length" ref="chartEl" class="kg-chart"></div>
    </NSpin>

    <div v-if="selected" class="kg-detail">
      <div class="kg-detail-head">
        <span class="kg-dot" :style="{ background: colorFor(selected.name) }"></span>
        <b>{{ selected.name }}</b>
        <NTag size="small" :bordered="false">频次 {{ selected.freq }}</NTag>
        <NTag size="small" :bordered="false" type="info">文档 {{ selected.doc_count }}</NTag>
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
  padding: 4px 2px 14px;
}
.kg-stat { font-size: 13px; color: $text-secondary; b { color: $text-primary; } }
.kg-tip { font-size: 11px; color: $text-muted; margin-left: auto; }
.kg-empty { padding: 80px 0; }
.kg-chart {
  width: 100%;
  height: calc(100vh - 220px);
  min-height: 460px;
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
.kg-detail-head { display: flex; align-items: center; gap: 8px; }
.kg-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.kg-detail-rel { margin-top: 8px; font-size: 12px; color: $text-secondary; }
.rel-tag { margin: 2px 4px 2px 0; }
.muted { color: $text-muted; }
</style>
