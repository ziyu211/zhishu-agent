<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { NButton } from 'naive-ui'
import type { Msg } from '@/stores/chat'
import {
  reasoningToSteps,
  sessionReasoning,
  sessionAgentTrace,
  extractConcepts,
  buildBaseGraph,
  buildConceptMessageMap,
  createForceSimulation,
  nodeRadius,
  NUCLEUS_ID,
  sessionDelegations,
  buildAgentGraph,
  buildDelegationCellGraph,
  type ThinkingStep,
  type ConceptGraph,
  type ConceptNode,
  type SimNode,
  type AgentGraphNode,
  type DelegStatus,
  type AgentDelegation,
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
    const v = localStorage.getItem(TAB_KEY)
    if (v === 'steps' || v === 'concepts') return v
    // 默认展示「概念图谱」：即使模型尚未思考，也能即时预览「细胞」式基础点
    return 'concepts'
  } catch {
    return 'concepts'
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
// 多智能体委派推理链（主智能体的思考与推理链）优先于模型 <think> 文本
const hasTrace = computed(() => sessionAgentTrace(props.messages).length > 0)
const reasoning = computed(() => sessionReasoning(props.messages))
const steps = computed<ThinkingStep[]>(() => {
  const trace = sessionAgentTrace(props.messages)
  return trace.length ? trace : reasoningToSteps(reasoning.value)
})
/* ── 多智能体协作调用图（call graph）：谁发出 → 调用了哪个 agent → 是否出结果 ── */
const delegations = computed<AgentDelegation[]>(() => sessionDelegations(props.messages))
const hasDelegation = computed(() => delegations.value.length > 0)
const agentGraphFull = computed(() => (hasDelegation.value ? buildAgentGraph(delegations.value) : { nodes: [], edges: [] }))
const agentMeta = computed(() => {
  const m = new Map<string, AgentGraphNode>()
  for (const n of agentGraphFull.value.nodes) m.set(n.id, n)
  return m
})
const agentEdgeStatus = computed(() => {
  const m = new Map<string, DelegStatus>()
  for (const e of agentGraphFull.value.edges) m.set(`${e.source}|${e.target}`, e.status)
  return m
})

/* 默认 Tab 自适应：会话已有委派推理链但【未】建多智能体团队 → 展示「推理步骤」；
 * 已建团队（含委派关系）→ 停留在「概念图谱」直接呈现细胞气泡流程图。
 * 注意：必须在 hasTrace / hasDelegation 均已声明之后再求值（否则触发 TDZ，
 * 整个组件 setup 抛错、面板无法渲染）。会话为异步加载，故用 watch 首次命中即定，
 * 之后尊重用户手动切换。 */
let tabAutoDecided = false
watch(
  [hasTrace, hasDelegation],
  ([trace, deleg]) => {
    if (tabAutoDecided) return
    if (!trace && !deleg) return
    tabAutoDecided = true
    if (trace && !deleg && tab.value === 'concepts') tab.value = 'steps'
  },
  { immediate: true },
)
// 摘要列表用：截断任务文本
const summarizeTask = (t: string, n = 22) => {
  const c = (t || '').replace(/\s+/g, ' ').trim()
  return c.length > n ? c.slice(0, n) + '…' : c
}
const statusGlyph = (s?: string | null) =>
  s === 'done' ? '✓' : s === 'error' || s === 'timeout' ? '✗' : s === 'running' ? '…' : s === 'empty' ? '○' : '·'

/* 概念图谱：优先用模型思考抽取的概念；若会话尚无思考，则退化为
   基于提问的「细胞」式基础图谱，保证随时都能看到可交互的图。
   若会话含多智能体委派关系，则本 Tab 切换为「协作调用图」呈现。 */
const reasoningGraph = computed<ConceptGraph>(() => extractConcepts(reasoning.value))
const baseGraph = computed<ConceptGraph>(() => buildBaseGraph(props.messages))
const isPreview = computed(() => reasoningGraph.value.nodes.length === 0)
const conceptFinal = computed<ConceptGraph>(() => (isPreview.value ? baseGraph.value : reasoningGraph.value))
const graph = computed<ConceptGraph>(() =>
  hasDelegation.value ? buildDelegationCellGraph(delegations.value) : conceptFinal.value,
)
const inDelegation = computed(() => hasDelegation.value)
const conceptMsgMap = computed(() =>
  hasDelegation.value ? new Map<string, string>() : buildConceptMessageMap(props.messages, graph.value.nodes),
)
const nucleusTargetId = computed(() => {
  const u = [...props.messages].reverse().find((m) => m.role === 'user')
  return u?.id
})
const isNucleus = (id: string) => !inDelegation.value && id === NUCLEUS_ID

/* 委派模式下的节点 / 边样式辅助 */
const nodeRoleOf = (id: string) => agentMeta.value.get(id)?.role ?? 'unknown'
const nodeStatusOf = (id: string) => {
  const gn = graph.value.nodes.find((n) => n.id === id)
  if (gn?.status) return gn.status
  return agentMeta.value.get(id)?.status
}
const edgeStatusOf = (e: { source: string; target: string }) =>
  agentEdgeStatus.value.get(`${e.source}|${e.target}`)
const nodeClass = (id: string) => {
  if (!inDelegation.value) return ''
  const gn = graph.value.nodes.find((n) => n.id === id)
  const r = gn?.role ?? nodeRoleOf(id)
  const s = nodeStatusOf(id)
  return [gn?.isEnd ? 'is-end' : '', `role-${r}`, `st-${s ?? 'idle'}`].filter(Boolean).join(' ')
}
const isAgentNode = (id: string) => graph.value.nodes.find((n) => n.id === id)?.kind === 'agent'
const isEndNode = (id: string) => graph.value.nodes.find((n) => n.id === id)?.isEnd === true
const nodeTitle = (n: ConceptNode): string => {
  if (n.kind === 'agent') {
    const r =
      n.role === 'root' ? '主智能体' : n.role === 'coordinator' ? '管理 agent' : n.role === 'executor' ? '具体 agent' : '智能体'
    const s = statusGlyph(n.status) + ' ' + (n.status || 'idle')
    return `${n.isEnd ? '流程结束 · ' : ''}${r} · ${s}`
  }
  return isNucleus(n.id) ? '对话主题 · 点击跳转到提问' : conceptMsgMap.value.get(n.id) ? '点击跳转到相关对话' : n.label
}
const edgeArrowStatus = (e: { source: string; target: string }): string => {
  const s = edgeStatusOf(e)
  return s ?? 'default'
}
const edgeClass = (e: { source: string; target: string }) => {
  const base = edgeVisible(e) ? '' : 'dim'
  if (!inDelegation.value) return base
  const s = edgeStatusOf(e) ?? 'running'
  return [base, `st-${s}`].filter(Boolean).join(' ')
}

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
  const w = el.clientWidth || 320
  const h = el.clientHeight || 420
  // 容器尚未展开时先用默认尺寸，避免力导向在 0x0 区域初始化导致节点不可见
  W.value = w > 0 ? w : 320
  H.value = h > 0 ? h : 420
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
  // 委派模式使用确定性径向布局（delegLayout），无需力导向引擎
  if (inDelegation.value) {
    sim = null
    return
  }
  const g = graph.value
  if (!g.nodes.length) {
    sim = null
    return
  }
  // 保险：尺寸不可用时回退到默认，防止节点被压缩到不可见
  const w = W.value > 0 ? W.value : 320
  const h = H.value > 0 ? H.value : 420
  W.value = w
  H.value = h
  sim = createForceSimulation(g.nodes, g.edges, { width: w, height: h })
  ticks = 0
  startLoop()
}

const edgeVisible = (e: { source: string; target: string }) => {
  if (!hoverId.value) return true
  return e.source === hoverId.value || e.target === hoverId.value
}
const nodePos = (id: string): SimNode => {
  const n = sim?.getNode(id)
  if (n) return n
  // 力导向尚未就绪时给出一个可见的中心占位，避免 SVG 坐标 undefined 导致节点消失
  return { id, x: W.value / 2, y: H.value / 2, vx: 0, vy: 0, weight: 1 }
}

/* ── 委派模式：确定性径向布局（不依赖力导向，加载即出图、位置稳定）──
 * 主核(主管)居中；管理 agent 内环；具体 agent 外环均布；结束气泡置于下方。
 * 彻底规避力导向在容器 0 尺寸 / 异步时序下不渲染导致「只剩图例、无气泡」的问题。 */
const delegLayout = computed<Record<string, { x: number; y: number }>>(() => {
  const out: Record<string, { x: number; y: number }> = {}
  const g = graph.value
  if (!inDelegation.value || !g.nodes.length) return out
  const w = W.value > 0 ? W.value : 320
  const h = H.value > 0 ? H.value : 420
  const cx = w / 2
  const cy = h / 2
  const r1 = Math.min(w, h) * 0.26
  const r2 = Math.min(w, h) * 0.40
  const root = g.nodes.find((n) => n.role === 'root')
  const coords = g.nodes.filter((n) => n.role === 'coordinator')
  const execs = g.nodes.filter((n) => n.role === 'executor')
  const end = g.nodes.find((n) => n.isEnd)
  if (root) out[root.id] = { x: cx, y: cy }
  coords.forEach((n, i) => {
    const a = -Math.PI / 2 + (i - (coords.length - 1) / 2) * 0.6
    out[n.id] = { x: cx + Math.cos(a) * r1, y: cy + Math.sin(a) * r1 }
  })
  execs.forEach((n, i) => {
    const a = -Math.PI / 2 + (i / Math.max(1, execs.length)) * Math.PI * 2
    out[n.id] = { x: cx + Math.cos(a) * r2, y: cy + Math.sin(a) * r2 }
  })
  if (end) out[end.id] = { x: cx, y: cy + r2 }
  return out
})
const posOfId = (id: string): { x: number; y: number } =>
  inDelegation.value ? (delegLayout.value[id] || { x: W.value / 2, y: H.value / 2 }) : nodePos(id)
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
function scheduleRebuild(immediate = false) {
  if (tab.value !== 'concepts') return
  window.clearTimeout(rebuildTimer)
  if (immediate) {
    measure()
    buildSim()
    return
  }
  rebuildTimer = window.setTimeout(() => {
    measure()
    buildSim()
  }, 150)
}

watch([graph, tab], async () => {
  await nextTick()
  if (tab.value === 'concepts') scheduleRebuild(true)
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
let ro: ResizeObserver | null = null
onMounted(async () => {
  await nextTick()
  measure()
  if (tab.value === 'concepts') buildSim()
  window.addEventListener('resize', onResize)
  // 容器尺寸变化（含 v-if 挂载后布局稳定、面板开合、Tab 切换）时重新测量，
  // 保证委派径向布局始终基于真实尺寸，避免 0 尺寸下气泡不可见。
  if (graphBox.value && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      if (tab.value !== 'concepts') return
      measure()
      if (!inDelegation.value) buildSim()
    })
    ro.observe(graphBox.value)
  }
})
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.clearTimeout(rebuildTimer)
  window.removeEventListener('resize', onResize)
  ro?.disconnect()
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
      <!-- 推理步骤 -->
      <div v-if="tab === 'steps'" class="tg-steps">
        <div v-if="!hasReasoning && !hasTrace" class="tg-empty">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z" />
          </svg>
          <p>尚未产生思考 / 委派链路</p>
          <span v-if="!hasReasoning && !hasTrace">当模型以 &lt;think&gt; 输出深度思考，或由主管智能体委派子智能体协作时，这里会自动绘制推理步骤链。切到「概念图谱」即可基于当前提问即时预览。</span>
        </div>
        <div v-else-if="steps.length === 0" class="tg-empty">
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
                <span v-if="s.agent" class="tg-step-agent">{{ s.agent }}</span>
              </div>
              <p class="tg-step-text">{{ s.text }}</p>
            </div>
          </li>
        </ol>
      </div>

      <!-- 概念图谱：以细胞气泡呈现。含多智能体委派时，气泡表达
           主 agent 发出 → 管理 agent 委派 → 具体 agent → 结束 的完整流程 -->
      <div v-else class="tg-graph-wrap">
        <div v-if="graph.nodes.length === 0" class="tg-empty">
          <p>暂无可展示的概念 / 委派</p>
          <span v-if="!hasDelegation">思考文本过短或术语重复度低，暂不足以生成概念网络。</span>
          <span v-else>本次对话尚未触发多智能体委派。</span>
        </div>
        <div v-else ref="graphBox" class="tg-graph">
          <svg :width="W" :height="H" :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="xMidYMid meet" class="tg-svg">
            <defs>
              <marker id="arr-default" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L7,3 L0,6 Z" fill="#9aa0a6"/></marker>
              <marker id="arr-done" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L7,3 L0,6 Z" fill="#2e9e5b"/></marker>
              <marker id="arr-error" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L7,3 L0,6 Z" fill="#e8453c"/></marker>
              <marker id="arr-running" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L7,3 L0,6 Z" fill="#d9a441"/></marker>
              <marker id="arr-empty" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L7,3 L0,6 Z" fill="#9aa0a6"/></marker>
            </defs>
            <g class="tg-edges">
                <line
                v-for="(e, i) in graph.edges"
                :key="`e-${i}`"
                :x1="posOfId(e.source).x"
                :y1="posOfId(e.source).y"
                :x2="posOfId(e.target).x"
                :y2="posOfId(e.target).y"
                class="tg-edge"
                :class="[edgeClass(e), { hot: hoverId && (e.source === hoverId || e.target === hoverId) }]"
                :marker-end="isAgentNode(e.source) ? `url(#arr-${edgeArrowStatus(e)})` : null"
              />
            </g>
            <g class="tg-nodes">
              <g
                v-for="n in graph.nodes"
                :key="n.id"
                class="tg-node"
                :class="[nodeClass(n.id), { dim: nodeDimmed(n.id), 'tg-node--nucleus': isNucleus(n.id) }]"
                :style="{ cursor: (isNucleus(n.id) || (!isAgentNode(n.id) && conceptMsgMap.get(n.id))) ? 'pointer' : 'grab' }"
                @pointerenter="hoverId = n.id"
                @pointerleave="hoverId = null"
                @pointerdown="onNodeDown($event, n.id)"
                @pointermove="onNodeMove"
                @pointerup="onNodeUp"
              >
                <title>{{ nodeTitle(n) }}</title>
                <!-- 细胞气泡膜（脉冲呼吸环）：智能体带角色色膜；概念图细胞核带品牌色膜 -->
                <circle
                  v-if="isAgentNode(n.id)"
                  :cx="posOfId(n.id).x"
                  :cy="posOfId(n.id).y"
                  :r="nodeRadius(n.weight, maxWeight) + 6"
                  class="tg-membrane"
                  :class="`role-${n.role}`"
                />
                <circle
                  v-if="isNucleus(n.id)"
                  :cx="posOfId(n.id).x"
                  :cy="posOfId(n.id).y"
                  :r="nodeRadius(n.weight, maxWeight) + 7"
                  class="tg-membrane"
                />
                <circle
                  :cx="posOfId(n.id).x"
                  :cy="posOfId(n.id).y"
                  :r="nodeRadius(n.weight, maxWeight)"
                  class="tg-node-dot"
                  :class="isAgentNode(n.id) ? `role-${n.role}` : ''"
                />
                <text
                  v-if="isAgentNode(n.id)"
                  :x="posOfId(n.id).x"
                  :y="posOfId(n.id).y - nodeRadius(n.weight, maxWeight) - 5"
                  class="tg-node-status"
                >{{ statusGlyph(nodeStatusOf(n.id)) }}</text>
                <text
                  :x="posOfId(n.id).x"
                  :y="posOfId(n.id).y + nodeRadius(n.weight, maxWeight) + 11"
                  class="tg-node-label"
                >{{ n.label }}</text>
              </g>
            </g>
          </svg>
          <div class="tg-graph-stat">
            <template v-if="isPreview && !hasDelegation">预览 · 基于提问生成</template>
            <template v-else-if="hasDelegation">{{ graph.nodes.length - 1 }} 个智能体 · {{ graph.edges.length }} 条委派</template>
            <template v-else>{{ graph.nodes.length }} 个概念 · {{ graph.edges.length }} 条关联</template>
          </div>
        </div>
        <!-- 图例（气泡小圆点风格，仅委派模式） -->
        <div v-if="hasDelegation" class="tg-legend">
          <span class="lg-dot role-root"></span><span>主智能体</span>
          <span class="lg-dot role-coordinator"></span><span>管理 agent</span>
          <span class="lg-dot role-executor"></span><span>具体 agent</span>
          <span class="lg-dot st-done"></span><span>✓ 已出结果</span>
          <span class="lg-dot st-running"></span><span>… 进行中</span>
          <span class="lg-dot st-error"></span><span>✗ 失败/超时</span>
          <span class="lg-dot st-empty"></span><span>○ 无返回</span>
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
.tg-step-agent {
  font-size: 10px;
  font-weight: 600;
  color: var(--brand, #e8453c);
  background: rgba(var(--brand-rgb, 232 69 60), 0.12);
  padding: 1px 7px;
  border-radius: 999px;
  letter-spacing: 0.2px;
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
.tg-graph-wrap { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.tg-graph { position: relative; flex: 1; overflow: hidden; touch-action: none; min-height: 260px; }
.tg-svg { display: block; width: 100%; height: 100%; cursor: grab; }
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

/* ── 细胞气泡：多智能体委派流程（保留气泡视觉，表达 主→管理→具体→结束） ── */
/* 气泡膜（脉冲呼吸环）：智能体带角色色膜，概念图细胞核带品牌色膜 */
.tg-membrane.role-root { stroke: var(--brand, #e8453c); }
.tg-membrane.role-coordinator { stroke: #2f7de1; }
.tg-membrane.role-executor { stroke: #1f9d7a; }
.tg-membrane.is-end { stroke: #2e9e5b; }
/* 智能体节点填充（角色配色） */
.tg-node-dot.role-root { fill: var(--brand, #e8453c); fill-opacity: 0.92; }
.tg-node-dot.role-coordinator { fill: #2f7de1; fill-opacity: 0.9; }
.tg-node-dot.role-executor { fill: #1f9d7a; fill-opacity: 0.9; }
/* 结束气泡：覆盖 root 红为绿，强调流程终点 */
.tg-node.is-end .tg-node-dot { fill: #2e9e5b; fill-opacity: 0.95; }
.tg-node.is-end .tg-membrane { stroke: #2e9e5b; }
.tg-node.is-end .tg-node-label { fill: #2e9e5b; font-weight: 600; }
/* 状态描边 + 状态角标 */
.tg-node.st-done .tg-node-dot { stroke: #2e9e5b; stroke-width: 2; }
.tg-node.st-error .tg-node-dot,
.tg-node.st-timeout .tg-node-dot { stroke: var(--brand, #e8453c); stroke-width: 2; }
.tg-node.st-running .tg-node-dot { stroke: #d9a441; stroke-width: 2; stroke-dasharray: 3 2; }
.tg-node.st-empty .tg-node-dot,
.tg-node.st-idle .tg-node-dot { stroke: #9aa0a6; stroke-width: 1.5; }
.tg-node-status { font-size: 11px; font-weight: 700; text-anchor: middle; pointer-events: none; }
.tg-node.st-done .tg-node-status { fill: #2e9e5b; }
.tg-node.st-error .tg-node-status,
.tg-node.st-timeout .tg-node-status { fill: var(--brand, #e8453c); }
.tg-node.st-running .tg-node-status { fill: #d9a441; }
.tg-node.st-empty .tg-node-status,
.tg-node.st-idle .tg-node-status { fill: #9aa0a6; }
/* 边：状态色 + 方向箭头（箭头颜色由 <marker> 定义匹配） */
.tg-edge.st-done { stroke: #2e9e5b; stroke-opacity: 0.5; }
.tg-edge.st-error,
.tg-edge.st-timeout { stroke: var(--brand, #e8453c); stroke-opacity: 0.6; }
.tg-edge.st-running { stroke: #d9a441; stroke-opacity: 0.5; stroke-dasharray: 4 3; }
.tg-edge.st-empty { stroke: #9aa0a6; stroke-opacity: 0.4; }
/* 图例（气泡小圆点风格） */
.tg-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 9px; padding: 6px 12px 8px; font-size: 10px; color: $text-secondary; border-top: 1px solid $border-color; }
.tg-legend .lg-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.tg-legend .lg-dot.role-root { background: var(--brand, #e8453c); }
.tg-legend .lg-dot.role-coordinator { background: #2f7de1; }
.tg-legend .lg-dot.role-executor { background: #1f9d7a; }
.tg-legend .lg-dot.st-done { background: #2e9e5b; }
.tg-legend .lg-dot.st-running { background: #d9a441; }
.tg-legend .lg-dot.st-error { background: var(--brand, #e8453c); }
.tg-legend .lg-dot.st-empty { background: #9aa0a6; }

</style>
