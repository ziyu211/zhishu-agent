<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { NButton } from 'naive-ui'
import type { Msg } from '@/stores/chat'
import {
  reasoningToSteps,
  sessionReasoning,
  extractConcepts,
  buildBaseGraph,
  buildConceptMessageMap,
  createForceSimulation,
  nodeRadius,
  NUCLEUS_ID,
  type ThinkingStep,
  type ConceptGraph,
  type SimNode,
} from '@/composables/useThinkingGraph'

const props = defineProps<{ messages: Msg[] }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'focus-message', id: string): void
}>()

/* ── Tab 状态（持久化到 localStorage，跨刷新保持） ── */
const TAB_KEY = 'zhishu:tgTab'
function loadTab(): 'steps' | 'concepts' {
  try {
    return localStorage.getItem(TAB_KEY) === 'concepts' ? 'concepts' : 'steps'
  } catch {
    return 'steps'
  }
}
const tab = ref<'steps' | 'concepts'>(loadTab())
watch(tab, (v) => {
  try {
    localStorage.setItem(TAB_KEY, v)
  } catch {
    /* ignore */
  }
})

/* ── 数据源 ───────────────────────────────── */
const hasReasoning = computed(() =>
  props.messages.some((m) => m.role === 'assistant' && m.reasoning && m.reasoning.trim()),
)
const reasoning = computed(() => sessionReasoning(props.messages))
const steps = computed<ThinkingStep[]>(() => reasoningToSteps(reasoning.value))

/* 概念图谱：优先用模型思考抽取的概念；若会话尚无思考，则退化为
   基于提问的「细胞」式基础图谱，保证随时都能看到可交互的图。 */
const reasoningGraph = computed<ConceptGraph>(() => extractConcepts(reasoning.value))
const baseGraph = computed<ConceptGraph>(() => buildBaseGraph(props.messages))
const isPreview = computed(() => reasoningGraph.value.nodes.length === 0)
const graph = computed<ConceptGraph>(() => (isPreview.value ? baseGraph.value : reasoningGraph.value))
const conceptMsgMap = computed(() => buildConceptMessageMap(props.messages, graph.value.nodes))

/* 点击中心核时跳转的对话消息（最新一条用户提问） */
const nucleusTargetId = computed(() => {
  const u = [...props.messages].reverse().find((m) => m.role === 'user')
  return u?.id
})
const isNucleus = (id: string) => id === NUCLEUS_ID

const KIND_LABEL: Record<string, string> = {
  goal: '目标',
  observe: '观察',
  analyze: '分析',
  plan: '计划',
  action: '行动',
  conclude: '结论',
  note: '备注',
}
const KIND_ICON: Record<string, string> = {
  goal: 'M12 2v4M12 18v4M2 12h4M18 12h4',
  observe: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z',
  analyze: 'M21 21l-4.3-4.3M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z',
  plan: 'M9 5h11M9 12h11M9 19h11M4 5h.01M4 12h.01M4 19h.01',
  action: 'M13 2L3 14h7l-1 8 10-12h-7l1-8z',
  conclude: 'M20 6L9 17l-5-5',
  note: 'M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z',
}

/* ── 概念图谱：力导向布局 ────────────────────── */
const graphBox = ref<HTMLElement | null>(null)
const W = ref(320)
const H = ref(420)
const frame = ref(0)
const maxWeight = computed(() => Math.max(1, ...graph.value.nodes.map((n) => n.weight)))
const hoverId = ref<string | null>(null)

let sim: ReturnType<typeof createForceSimulation> | null = null
let raf = 0
let running = false
let ticks = 0

function measure() {
  const el = graphBox.value
  if (!el) return
  W.value = el.clientWidth || 320
  H.value = el.clientHeight || 420
}

function loop() {
  if (!sim) return
  sim.tick()
  frame.value++
  ticks++
  if (sim.temperature > 0.1 && ticks < 900) {
    raf = requestAnimationFrame(loop)
  } else {
    running = false
  }
}
function startLoop() {
  if (running || !sim) return
  running = true
  raf = requestAnimationFrame(loop)
}
function buildSim() {
  cancelAnimationFrame(raf)
  running = false
  const g = graph.value
  if (!g.nodes.length) {
    sim = null
    return
  }
  sim = createForceSimulation(g.nodes, g.edges, { width: W.value, height: H.value })
  ticks = 0
  startLoop()
}

const edgeVisible = (e: { source: string; target: string }) => {
  if (!hoverId.value) return true
  return e.source === hoverId.value || e.target === hoverId.value
}
const nodePos = (id: string): SimNode | undefined => sim?.getNode(id)
const nodeDimmed = (id: string) => !!hoverId.value && hoverId.value !== id

/* ── 拖拽节点（区分点击：未移动则视为点击跳转） ── */
let dragId: string | null = null
let downX = 0
let downY = 0
let moved = false
function onNodeDown(e: PointerEvent, id: string) {
  dragId = id
  downX = e.clientX
  downY = e.clientY
  moved = false
  const n = sim?.getNode(id)
  if (n) n.fixed = true
  ;(e.target as Element).setPointerCapture?.(e.pointerId)
}
function onNodeMove(e: PointerEvent) {
  if (!dragId || !sim || !graphBox.value) return
  if (Math.abs(e.clientX - downX) > 4 || Math.abs(e.clientY - downY) > 4) moved = true
  const rect = graphBox.value.getBoundingClientRect()
  const n = sim.getNode(dragId)
  if (!n) return
  n.x = (e.clientX - rect.left) * (W.value / rect.width)
  n.y = (e.clientY - rect.top) * (H.value / rect.height)
  sim.reheat()
  startLoop()
}
function onNodeUp() {
  if (dragId && sim) {
    const n = sim.getNode(dragId)
    if (n) n.fixed = false
    // 视为点击（非拖拽）：跳转到对应对话消息
    if (!moved) {
      if (dragId === NUCLEUS_ID) {
        if (nucleusTargetId.value) emit('focus-message', nucleusTargetId.value)
      } else {
        const mid = conceptMsgMap.value.get(dragId)
        if (mid) emit('focus-message', mid)
      }
    }
  }
  dragId = null
}

/* ── 响应数据 / 尺寸变化 ───────────────────── */
let rebuildTimer: number | undefined
function scheduleRebuild() {
  if (tab.value !== 'concepts') return
  window.clearTimeout(rebuildTimer)
  rebuildTimer = window.setTimeout(() => {
    measure()
    buildSim()
  }, 350)
}

watch([graph, tab], async () => {
  await nextTick()
  if (tab.value === 'concepts') scheduleRebuild()
})
watch(
  () => props.messages.length,
  () => scheduleRebuild(),
)

function onResize() {
  if (tab.value === 'concepts') {
    measure()
    buildSim()
  }
}
onMounted(async () => {
  await nextTick()
  measure()
  if (tab.value === 'concepts') buildSim()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.clearTimeout(rebuildTimer)
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <aside class="tg-panel">
    <header class="tg-header">
      <div class="tg-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="7" r="2.5" /><circle cx="12" cy="18" r="2.5" />
          <path d="M8 7l8 0M7 8l4 8M17 9l-4 7" />
        </svg>
        <span>思维图谱</span>
      </div>
      <NButton quaternary size="tiny" circle @click="emit('close')">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </template>
      </NButton>
    </header>

    <div class="tg-tabs">
      <button class="tg-tab" :class="{ active: tab === 'steps' }" @click="tab = 'steps'">推理步骤</button>
      <button class="tg-tab" :class="{ active: tab === 'concepts' }" @click="tab = 'concepts'">概念图谱</button>
    </div>

    <div class="tg-body">
      <!-- 空态：整会话都没有思考过程 -->
      <div v-if="!hasReasoning" class="tg-empty">
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z" />
        </svg>
        <p>模型尚未产生思考过程</p>
        <span>当模型以 &lt;think&gt; 输出深度思考时，这里会自动绘制推理链路与概念网络。</span>
      </div>

      <!-- 推理步骤 -->
      <div v-else-if="tab === 'steps'" class="tg-steps">
        <div v-if="steps.length === 0" class="tg-empty">
          <p>暂无可解析的步骤</p>
          <span>思考文本中没有明显的编号或过渡结构，暂无法拆为步骤链。</span>
        </div>
        <ol v-else class="tg-step-list">
          <li
            v-for="s in steps"
            :key="s.id"
            class="tg-step"
            :class="`kind-${s.kind}`"
            :style="{ marginLeft: s.depth * 14 + 'px' }"
          >
            <div class="tg-step-badge">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path :d="KIND_ICON[s.kind]" />
              </svg>
            </div>
            <div class="tg-step-content">
              <div class="tg-step-meta">
                <span class="tg-step-no">#{{ s.index }}</span>
                <span class="tg-step-kind">{{ KIND_LABEL[s.kind] }}</span>
              </div>
              <p class="tg-step-text">{{ s.text }}</p>
            </div>
          </li>
        </ol>
      </div>

      <!-- 概念图谱 -->
      <div v-else class="tg-graph-wrap">
        <div v-if="graph.nodes.length === 0" class="tg-empty">
          <p>概念不足，无法构图</p>
          <span>思考文本过短或术语重复度低，暂不足以生成概念网络。</span>
        </div>
        <div v-else ref="graphBox" class="tg-graph">
          <svg :width="W" :height="H" class="tg-svg">
            <g class="tg-edges">
              <line
                v-for="(e, i) in graph.edges"
                :key="`e-${i}`"
                :x1="nodePos(e.source)?.x"
                :y1="nodePos(e.source)?.y"
                :x2="nodePos(e.target)?.x"
                :y2="nodePos(e.target)?.y"
                class="tg-edge"
                :class="{ dim: !edgeVisible(e), hot: hoverId && (e.source === hoverId || e.target === hoverId) }"
              />
            </g>
            <g class="tg-nodes">
              <g
                v-for="n in graph.nodes"
                :key="n.id"
                class="tg-node"
                :class="{ dim: nodeDimmed(n.id), 'tg-node--nucleus': isNucleus(n.id) }"
                :style="{ cursor: isNucleus(n.id) || conceptMsgMap.get(n.id) ? 'pointer' : 'grab' }"
                @pointerenter="hoverId = n.id"
                @pointerleave="hoverId = null"
                @pointerdown="onNodeDown($event, n.id)"
                @pointermove="onNodeMove"
                @pointerup="onNodeUp"
              >
                <title v-if="isNucleus(n.id)">对话主题 · 点击跳转到提问</title>
                <title v-else-if="conceptMsgMap.get(n.id)">点击跳转到相关对话</title>
                <!-- 细胞核膜：脉冲呼吸环 -->
                <circle
                  v-if="isNucleus(n.id)"
                  :cx="nodePos(n.id)?.x"
                  :cy="nodePos(n.id)?.y"
                  :r="nodeRadius(n.weight, maxWeight) + 7"
                  class="tg-membrane"
                />
                <circle
                  :cx="nodePos(n.id)?.x"
                  :cy="nodePos(n.id)?.y"
                  :r="nodeRadius(n.weight, maxWeight)"
                  class="tg-node-dot"
                />
                <text
                  :x="nodePos(n.id)?.x"
                  :y="(nodePos(n.id)?.y ?? 0) + nodeRadius(n.weight, maxWeight) + 11"
                  class="tg-node-label"
                >{{ n.label }}</text>
              </g>
            </g>
          </svg>
          <div class="tg-graph-stat">
            <template v-if="isPreview">预览 · 基于提问生成</template>
            <template v-else>{{ graph.nodes.length }} 个概念 · {{ graph.edges.length }} 条关联</template>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.tg-panel {
  width: 320px;
  flex-shrink: 0;
  border-left: 1px solid $border-color;
  display: flex;
  flex-direction: column;
  background: $bg-card;
  overflow: hidden;
  transition: width $transition-normal, opacity $transition-normal;

  @media (max-width: $breakpoint-mobile) {
    position: absolute;
    right: 0;
    top: 0;
    height: 100%;
    z-index: 11;
    box-shadow: -2px 0 10px rgba(0, 0, 0, 0.12);
  }
}

.tg-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 10px;
  flex-shrink: 0;
}
.tg-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
  svg { color: $accent-primary; }
}

.tg-tabs {
  display: flex;
  gap: 4px;
  padding: 0 12px 10px;
  flex-shrink: 0;
}
.tg-tab {
  flex: 1;
  padding: 7px 0;
  border: 1px solid $border-color;
  background: $bg-input;
  color: $text-secondary;
  border-radius: $radius-sm;
  font-size: 12px;
  cursor: pointer;
  transition: all $transition-fast;
  &:hover { color: $text-primary; border-color: $accent-muted; }
  &.active {
    background: $accent-primary;
    color: var(--text-on-accent);
    border-color: $accent-primary;
  }
}

.tg-body { flex: 1; overflow: hidden; display: flex; }

.tg-empty {
  margin: auto;
  padding: 30px 22px;
  text-align: center;
  color: $text-muted;
  svg { color: $accent-muted; margin-bottom: 10px; }
  p { font-size: 13px; color: $text-secondary; margin: 0 0 6px; font-weight: 500; }
  span { font-size: 11.5px; line-height: 1.6; display: block; }
}

/* ── 步骤 ── */
.tg-steps { flex: 1; overflow-y: auto; padding: 6px 14px 18px; }
.tg-step-list { list-style: none; margin: 0; padding: 0; position: relative; }
.tg-step {
  position: relative;
  padding: 0 0 16px 30px;
  &::before {
    content: '';
    position: absolute;
    left: 11px;
    top: 22px;
    bottom: -2px;
    width: 1.5px;
    background: $border-color;
  }
  &:last-child::before { display: none; }
}
.tg-step-badge {
  position: absolute;
  left: 0;
  top: 2px;
  width: 23px;
  height: 23px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-secondary);
  border: 1px solid $border-color;
  color: $text-secondary;
  svg { color: $accent-primary; }
}
.tg-step-content { padding-top: 1px; }
.tg-step-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.tg-step-no { font-size: 10px; color: $text-muted; font-variant-numeric: tabular-nums; }
.tg-step-kind {
  font-size: 10px;
  font-weight: 600;
  color: $accent-primary;
  background: var(--bg-secondary);
  padding: 1px 6px;
  border-radius: 999px;
}
.tg-step-text {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: $text-secondary;
  white-space: pre-wrap;
  word-break: break-word;
}
/* 结论用品牌红强调 */
.kind-conclude .tg-step-badge { border-color: var(--brand); svg { color: var(--brand); } }
.kind-conclude .tg-step-kind { color: var(--brand); background: rgba(var(--brand-rgb), 0.1); }

/* ── 概念图谱 ── */
.tg-graph-wrap { flex: 1; overflow: hidden; display: flex; }
.tg-graph { position: relative; flex: 1; overflow: hidden; touch-action: none; }
.tg-svg { display: block; cursor: grab; }
.tg-edge {
  stroke: var(--accent-primary);
  stroke-opacity: 0.18;
  stroke-width: 1;
  transition: stroke-opacity $transition-fast;
  &.hot { stroke-opacity: 0.55; stroke-width: 1.4; }
  &.dim { stroke-opacity: 0.06; }
}
.tg-node { cursor: grab; }
.tg-node-dot {
  fill: var(--accent-primary);
  fill-opacity: 0.82;
  stroke: var(--bg-card);
  stroke-width: 1.5;
  transition: fill-opacity $transition-fast, opacity $transition-fast;
}
.tg-node-label {
  fill: $text-secondary;
  font-size: 10px;
  text-anchor: middle;
  pointer-events: none;
  paint-order: stroke;
  stroke: var(--bg-card);
  stroke-width: 3px;
  stroke-linejoin: round;
}
.tg-node.dim { opacity: 0.32; }
.tg-node.dim .tg-node-label { opacity: 0.4; }

/* 「细胞核」基础点：品牌色 + 脉冲膜 */
.tg-node--nucleus .tg-node-dot {
  fill: var(--brand, #e8453c);
  fill-opacity: 0.92;
  stroke: var(--bg-card);
  stroke-width: 2;
}
.tg-node--nucleus .tg-node-label {
  fill: var(--text-primary, #1f2329);
  font-size: 11px;
  font-weight: 600;
}
.tg-membrane {
  fill: none;
  stroke: var(--brand, #e8453c);
  stroke-opacity: 0.35;
  stroke-width: 1.4;
  transform-box: fill-box;
  transform-origin: center;
  animation: tg-pulse 2.6s ease-in-out infinite;
}
@keyframes tg-pulse {
  0% { stroke-opacity: 0.45; transform: scale(0.92); }
  50% { stroke-opacity: 0.12; transform: scale(1.16); }
  100% { stroke-opacity: 0.45; transform: scale(0.92); }
}

.tg-graph-stat {
  position: absolute;
  left: 10px;
  bottom: 8px;
  font-size: 10px;
  color: $text-muted;
  background: var(--bg-secondary);
  padding: 2px 7px;
  border-radius: 999px;
  pointer-events: none;
}
</style>
