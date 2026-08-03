/**
 * useThinkingGraph —— 思维知识图谱（MVP 前端版，零依赖）
 *
 * 提供两套视图所需的纯前端能力：
 *  1) reasoningToSteps   —— 把模型 <think> 思考过程切分为有序「推理步骤链」
 *  2) extractConcepts    —— 轻量术语共现抽取，产出「概念知识图谱」的节点与边
 *  3) createForceSimulation —— 零依赖力导向布局（用于概念图谱渲染动画）
 *
 * 全部在浏览器端运行，不引入任何 npm 依赖；抽取为启发式，足够在对话窗口
 * 内给用户一个「看见模型在想什么」的结构化视图。
 */
import type { Msg, AgentDelegation } from '@/stores/chat'

/* ───────────────────────── 推理步骤图 ───────────────────────── */

export type StepKind = 'goal' | 'observe' | 'analyze' | 'plan' | 'action' | 'conclude' | 'note'

export interface ThinkingStep {
  id: string
  index: number
  text: string
  kind: StepKind
  /** 编号层级（如 "1.2.3" → 2），用于缩进展示 */
  depth: number
  /** 该步骤归属的子智能体（多 Agent 委派场景），用于步骤上展示智能体标签 */
  agent?: string
}

const KIND_KEYWORDS: Record<StepKind, RegExp> = {
  goal: /(目标|问题|任务|需要解决|我要|目的是|目标是|需求|目标是)/,
  observe: /(观察|看到|注意到|发现|输入|给定|已知|上下文|当前|现在)/,
  analyze: /(分析|考虑|因为|由于|原因是|对比|区别|本质|推断|判断|其实|换句话说)/,
  plan: /(计划|步骤|我先|策略|方案|应该|我们可以|按照|流程|先.*再)/,
  action: /(调用|执行|使用工具|运行|查询|获取|计算|读取|搜索|生成|调用工具)/,
  conclude: /(综上|因此|所以|结论|最终|答案是|结果|得出|总结|综上所述|由此可见)/,
  note: /(注意|提醒|假设|如果|可能|另外|补充|需要说明|值得一提)/,
}

function classifyKind(text: string): StepKind {
  for (const k of Object.keys(KIND_KEYWORDS) as StepKind[]) {
    if (KIND_KEYWORDS[k].test(text)) return k
  }
  return 'analyze'
}

function depthOf(marker: string): number {
  // "1.2.3" → 2；"1)" → 0；"" → 0
  const dots = (marker.match(/\./g) || []).length
  return Math.max(0, dots)
}

/** 把一段自由文本（思考过程）切分成有序步骤。 */
export function splitReasoning(reasoning: string): string[] {
  const text = (reasoning || '').trim()
  if (!text) return []

  const lines = text.split(/\r?\n/)

  // 1) 显式编号 / 「步骤 N」/ 「Step N」 作为边界
  const numbered = /^\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+|[a-zA-Z])\s*[.、)]\s+|^步骤\s*\d+|^Step\s*\d+/i
  const boundaries = lines
    .map((l, i) => ({ l, i, m: l.match(numbered) }))
    .filter((x) => x.m)

  if (boundaries.length >= 2) {
    const segs: string[] = []
    for (let b = 0; b < boundaries.length; b++) {
      const start = boundaries[b].i
      const end = b + 1 < boundaries.length ? boundaries[b + 1].i : lines.length
      const seg = lines.slice(start, end).join('\n').trim()
      if (seg) segs.push(seg)
    }
    if (segs.length) return mergeShort(segs, 2)
  }

  // 2) 过渡词切片：句首出现「首先/然后/接着/最后/综上…」
  const transition = /^(首先|第一|其次|然后|接着|随后|接下来|最后|综上|因此|所以|另外|不过|其实|换句话说|简单来说)[\s，,：:]/
  const tIdx = lines
    .map((l, i) => ({ l, i, hit: transition.test(l) }))
    .filter((x) => x.hit)
    .map((x) => x.i)
  if (tIdx.length >= 2) {
    const segs: string[] = []
    for (let t = 0; t < tIdx.length; t++) {
      const start = tIdx[t]
      const end = t + 1 < tIdx.length ? tIdx[t + 1] : lines.length
      const seg = lines.slice(start, end).join('\n').trim()
      if (seg) segs.push(seg)
    }
    if (segs.length) return mergeShort(segs, 2)
  }

  // 3) 退化为按句切分，再贪心合并成 4~8 段，避免碎步骤
  const sentences = text
    .split(/(?<=[。！？\n；;])/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (sentences.length <= 1) return [text]
  return chunkSentences(sentences, 6)
}

/** 把过短的分段（< 12 字）向上合并到前一段。 */
function mergeShort(segs: string[], minLen: number): string[] {
  const out: string[] = []
  for (const s of segs) {
    const prev = out[out.length - 1]
    if (prev && prev.length < minLen * 4 && s.length < 40) {
      out[out.length - 1] = `${prev}\n${s}`
    } else {
      out.push(s)
    }
  }
  return out
}

/** 把句子贪心合并成约 target 段、每段长度均衡的块。 */
function chunkSentences(sentences: string[], target: number): string[] {
  const total = sentences.reduce((a, s) => a + s.length, 0)
  const per = Math.max(40, Math.ceil(total / target))
  const out: string[] = []
  let buf = ''
  for (const s of sentences) {
    if (buf && buf.length + s.length > per) {
      out.push(buf)
      buf = s
    } else {
      buf = buf ? `${buf}${s}` : s
    }
  }
  if (buf) out.push(buf)
  return out
}

export function reasoningToSteps(reasoning: string): ThinkingStep[] {
  const raw = splitReasoning(reasoning)
  return raw.map((text, i) => {
    const marker = text.match(/^\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+|[a-zA-Z])\s*[.、)]\s+/)
    const depth = marker ? depthOf(marker[0]) : 0
    return {
      id: `step-${i}`,
      index: i + 1,
      text: text.replace(/^\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+|[a-zA-Z])\s*[.、)]\s+/, '').trim(),
      kind: classifyKind(text),
      depth,
    }
  })
}

/* ───────────────────────── 概念知识图谱 ───────────────────────── */

export interface ConceptNode {
  id: string
  label: string
  weight: number
  /** 节点语义：概念词（默认）或智能体（委派流程图）。 */
  kind?: 'concept' | 'agent'
  /** 智能体节点角色：root=主/coordinator=管理/executor=具体/unknown。 */
  role?: DelegRole
  /** 智能体节点状态（来自最近一次委派结果）。 */
  status?: DelegStatus | 'idle'
  /** 是否为「结束」流程气泡。 */
  isEnd?: boolean
}

export interface ConceptEdge {
  source: string
  target: string
  weight: number
}

export interface ConceptGraph {
  nodes: ConceptNode[]
  edges: ConceptEdge[]
}

const STOPWORDS = new Set([
  '我们', '他们', '它们', '这个', '那个', '一个', '一种', '一些', '这些', '那些',
  '可以', '需要', '进行', '通过', '因为', '所以', '但是', '如果', '那么', '以及',
  '同时', '并且', '然后', '因此', '这样', '那样', '什么', '怎么', '如何', '是否',
  '应该', '可能', '已经', '没有', '不是', '就是', '这样', '对于', '关于', '由于',
  'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'can', 'will', 'not',
  'but', 'you', 'our', 'its', 'has', 'have', 'was', 'were', 'into', 'than', 'then',
])

/**
 * 抽取候选术语（英文词 + 中文 n-gram 2~4 元），与 extractConcepts 内部逻辑一致。
 * 供 buildBaseGraph 复用：对用户提问做高频词统计，生成「细胞」式卫星节点。
 */
function extractCandidates(text: string): string[] {
  const src = (text || '').toLowerCase()
  const out: string[] = []
  if (!src.trim()) return out

  const enWords = src.match(/[a-z][a-z0-9_+-]{2,}/g) || []
  for (const w of enWords) {
    if (STOPWORDS.has(w)) continue
    out.push(w)
  }

  const cjkRuns = src.match(/[一-鿿]+/g) || []
  for (const run of cjkRuns) {
    if (run.length === 1) continue
    if (run.length <= 5 && !STOPWORDS.has(run)) out.push(run)
    for (let n = 2; n <= 4; n++) {
      for (let i = 0; i + n <= run.length; i++) {
        const g = run.slice(i, i + n)
        if (STOPWORDS.has(g)) continue
        if (/^[一-鿿]{2,4}$/.test(g)) out.push(g)
      }
    }
  }
  return out
}

/**
 * 预览图专用：从用户提问抽取「较干净」的关键词。
 * 与 extractCandidates 的滑动 n-gram 不同，这里在连词/助词边界切分中文，
 * 取 2~5 字整块（更接近真实词），避免「索系统与」这类碎片噪声。
 */
const Q_BOUNDARY = /[的与和是在对用并而及以将把被让使叫等对于关于通过由于因为所以但是如果然后接着更最]/g
function extractQuestionTerms(text: string): string[] {
  const src = (text || '').toLowerCase()
  const out: string[] = []
  if (!src.trim()) return out

  const en = src.match(/[a-z][a-z0-9_+-]{2,}/g) || []
  for (const w of en) {
    if (STOPWORDS.has(w)) continue
    out.push(w)
  }

  const runs = src.match(/[一-鿿]+/g) || []
  for (const run of runs) {
    const chunks = run.split(Q_BOUNDARY)
    for (const c of chunks) {
      const t = c.trim()
      if (t.length >= 2 && t.length <= 5 && !STOPWORDS.has(t)) out.push(t)
    }
  }
  return out
}

/**
 * 轻量术语抽取（无分词库）：
 *  - 英文：长度 ≥ 3 的单词；
 *  - 中文：CJK 连续串，生成 2~3 字 n-gram 作为候选词（重复出现的更可能是真实词）；
 *  - 过滤停用词与低频噪声；取词频最高的 topN 作为节点；
 *  - 以「句子/换行窗口」为单位统计共现，得到边（仅连接 topN 节点）。
 */
export function extractConcepts(text: string, topN = 28, maxEdges = 60): ConceptGraph {
  const src = (text || '').toLowerCase()
  if (!src.trim()) return { nodes: [], edges: [] }

  const freq = new Map<string, number>()

  // 英文词
  const enWords = src.match(/[a-z][a-z0-9_+-]{2,}/g) || []
  for (const w of enWords) {
    if (STOPWORDS.has(w)) continue
    freq.set(w, (freq.get(w) || 0) + 1)
  }

  // 中文 n-gram
  const cjkRuns = src.match(/[一-鿿]+/g) || []
  for (const run of cjkRuns) {
    if (run.length === 1) continue
    // 整串较短（≤5）直接作为候选（更可能是完整词）
    if (run.length <= 5 && !STOPWORDS.has(run)) {
      freq.set(run, (freq.get(run) || 0) + 1)
    }
    // 2~4 字滑动窗口（较长窗口可捕获「知识库检索」这类复合词）
    for (let n = 2; n <= 4; n++) {
      for (let i = 0; i + n <= run.length; i++) {
        const g = run.slice(i, i + n)
        if (STOPWORDS.has(g)) continue
        if (/^[一-鿿]{2,4}$/.test(g)) freq.set(g, (freq.get(g) || 0) + 1)
      }
    }
  }

  // 取 topN：优先频率 ≥2 的术语以降噪；若不足则放宽到 ≥1
  let ranked = [...freq.entries()].sort((a, b) => b[1] - a[1])
  let chosen = ranked.filter(([, c]) => c >= 2)
  if (chosen.length < 6) chosen = ranked // 语料太短，放宽阈值
  const top = chosen.slice(0, topN)
  const nodeSet = new Set(top.map(([t]) => t))

  const nodes: ConceptNode[] = top.map(([label, weight]) => ({ id: label, label, weight }))

  // 共现：以句子/换行为窗口
  const windows: string[][] = []
  const segs = src.split(/[\n。！？；;]/).map((s) => s.trim()).filter(Boolean)
  for (const seg of segs) {
    const present = [...nodeSet].filter((t) => seg.includes(t))
    if (present.length >= 2) windows.push(present)
  }

  const edgeMap = new Map<string, number>()
  for (const win of windows) {
    for (let i = 0; i < win.length; i++) {
      for (let j = i + 1; j < win.length; j++) {
        const a = win[i]
        const b = win[j]
        const key = a < b ? `${a}|${b}` : `${b}|${a}`
        edgeMap.set(key, (edgeMap.get(key) || 0) + 1)
      }
    }
  }

  const edges: ConceptEdge[] = [...edgeMap.entries()]
    .map(([key, weight]) => {
      const [source, target] = key.split('|')
      return { source, target, weight }
    })
    .sort((a, b) => b.weight - a.weight)
    .slice(0, maxEdges)

  return { nodes, edges }
}

/** 汇总一个会话里所有 assistant 消息的思考文本，用于图谱抽取。 */
export function sessionReasoning(messages: Msg[]): string {
  return messages
    .filter((m) => m.role === 'assistant' && m.reasoning && m.reasoning.trim())
    .map((m) => m.reasoning as string)
    .join('\n\n')
}

/**
 * 汇总一个会话里所有消息的「委派推理链」（agentTrace）。
 * 多智能体协作时，主智能体的每一步委派 / 子智能体工具调用 / 返回都被结构化为
 * ThinkingStep，这里按消息顺序拼合并重新编号，作为思维图谱「推理步骤」的优先数据源。
 */
export function sessionAgentTrace(messages: Msg[]): ThinkingStep[] {
  const out: ThinkingStep[] = []
  let i = 0
  for (const m of messages) {
    const trace = (m as { agentTrace?: ThinkingStep[] }).agentTrace
    if (trace && trace.length) {
      for (const st of trace) {
        out.push({ ...st, id: `tg_${i}`, index: ++i })
      }
    }
  }
  return out
}

/** 中心核节点 id——「细胞」式基础点，始终存在于概念图谱中。 */
export const NUCLEUS_ID = '__nucleus__'

/**
 * 生成「细胞」式基础图谱：始终存在一个中心核（对话主题）作为「细胞核」，
 * 由用户提问里的高频关键词作为卫星节点（「细胞器」）连到核。
 * 这样即使模型尚未输出 <think> 思考，也能立刻看到一张可交互、带力导向的图。
 */
export function buildBaseGraph(messages: Msg[]): ConceptGraph {
  const userMsgs = messages.filter(
    (m) => m.role === 'user' && m.content && m.content.trim(),
  )
  const nucleus: ConceptNode = {
    id: NUCLEUS_ID,
    label: deriveSeedLabel(userMsgs),
    weight: 6,
  }
  const nodes: ConceptNode[] = [nucleus]
  const edges: ConceptEdge[] = []

  if (userMsgs.length) {
    const text = userMsgs.map((m) => m.content).join('\n')
    const candidates = extractQuestionTerms(text)
    const freq = new Map<string, number>()
    for (const c of candidates) {
      if (STOPWORDS.has(c) || c.length < 2) continue
      freq.set(c, (freq.get(c) || 0) + 1)
    }
    // 去碎片化：若某词是另一（更长）候选词的子串，则丢弃，保留完整词
    const entries = [...freq.entries()]
    const kept = entries.filter(
      ([a]) => !entries.some(([b]) => b !== a && a.length < b.length && b.includes(a)),
    )
    const top = kept.sort((a, b) => b[1] - a[1]).slice(0, 10)
    for (const [label, w] of top) {
      const id = `q:${label}`
      if (id === NUCLEUS_ID) continue
      nodes.push({ id, label, weight: 1 + w })
      edges.push({ source: NUCLEUS_ID, target: id, weight: 1 })
    }
  }
  return { nodes, edges }
}

/** 从用户消息派生中心核标签（对话主题）。 */
function deriveSeedLabel(userMsgs: Msg[]): string {
  const src = (userMsgs[userMsgs.length - 1] || userMsgs[0])?.content?.trim() || ''
  if (!src) return '对话'
  // 去掉开头客套前缀，截到首个标点或前 6 字
  const stripped = src
    .replace(/^[\s#*>`\-[\]【】]*/, '')
    .replace(/^(请[问教]?|帮我|我想|我要|如何|怎么|为什么|能否|是否|麻烦|可以|能|试|求)/, '')
  const cut = (stripped.split(/[，。？！；;:\n]/)[0] || stripped).replace(/\s+/g, '')
  const label = cut.slice(0, 6)
  return label && label.length >= 2 ? label : '对话'
}

/** 文本是否包含某术语：英文按整词边界匹配，中文按子串匹配。 */
export function textContains(term: string, text: string): boolean {
  const lower = text.toLowerCase()
  if (/[a-z]/.test(term)) {
    const re = new RegExp(`(^|[^a-z0-9])${escapeRe(term)}([^a-z0-9]|$)`, 'i')
    return re.test(lower)
  }
  return lower.includes(term.toLowerCase())
}

/** 转义正则特殊字符，用于把术语安全地嵌入 RegExp。 */
function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 为概念图谱的每个节点（term）找到其首次出现的消息 id，
 * 用于「点击概念节点 → 跳转并高亮对应对话消息」。
 * @param messages 会话消息（含 assistant.reasoning）
 * @param nodes    概念图谱节点（来自 extractConcepts）
 * @returns Map<term, messageId>
 */
export function buildConceptMessageMap(
  messages: Msg[],
  nodes: ConceptNode[],
): Map<string, string> {
  const map = new Map<string, string>()
  if (!nodes.length) return map
  for (const m of messages) {
    if (m.role !== 'assistant' || !m.reasoning) continue
    for (const nd of nodes) {
      if (!map.has(nd.id) && textContains(nd.id, m.reasoning)) {
        map.set(nd.id, m.id)
      }
    }
  }
  return map
}

/* ───────────────────────── 零依赖力导向布局 ───────────────────────── */

export interface SimNode {
  id: string
  x: number
  y: number
  vx: number
  vy: number
  weight: number
  fixed?: boolean
}

export interface SimEdge {
  source: string
  target: string
  weight: number
}

export interface ForceSim {
  nodes: SimNode[]
  edges: SimEdge[]
  tick(): void
  reheat(): void
  getNode(id: string): SimNode | undefined
  temperature: number
}

export interface SimOptions {
  width: number
  height: number
  iterations?: number
}

/**
 * Fruchterman–Reingold 变体：斥力 + 边引力 + 向心力，带冷却温度。
 * 组件用 requestAnimationFrame 反复调用 tick() 即可产生动画。
 */
export function createForceSimulation(
  nodes: { id: string; weight: number }[],
  edges: SimEdge[],
  opts: SimOptions,
): ForceSim {
  const { width, height } = opts
  const n = nodes.length
  const k = Math.sqrt((width * height) / Math.max(1, n)) * 0.8 // 理想间距
  const cx = width / 2
  const cy = height / 2

  const simNodes: SimNode[] = nodes.map((nd, i) => {
    // 初始撒在圆环上，避免重叠
    const ang = (i / Math.max(1, n)) * Math.PI * 2
    const r = Math.min(width, height) * 0.3
    return {
      id: nd.id,
      x: cx + Math.cos(ang) * r + (Math.random() - 0.5) * 20,
      y: cy + Math.sin(ang) * r + (Math.random() - 0.5) * 20,
      vx: 0,
      vy: 0,
      weight: nd.weight,
    }
  })
  const byId = new Map(simNodes.map((s) => [s.id, s]))

  let temperature = Math.min(width, height) * 0.25
  const cooling = 0.97
  const damping = 0.85

  function tick(): void {
    if (temperature < 0.05) return
    // 斥力（全部节点两两）
    for (let i = 0; i < simNodes.length; i++) {
      const a = simNodes[i]
      for (let j = i + 1; j < simNodes.length; j++) {
        const b = simNodes[j]
        let dx = a.x - b.x
        let dy = a.y - b.y
        let dist = Math.hypot(dx, dy) || 0.01
        if (dist < 0.01) {
          dx = (Math.random() - 0.5) * 0.1
          dy = (Math.random() - 0.5) * 0.1
          dist = Math.hypot(dx, dy) || 0.01
        }
        const force = (k * k) / dist
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        a.vx += fx
        a.vy += fy
        b.vx -= fx
        b.vy -= fy
      }
    }
    // 边引力
    for (const e of edges) {
      const a = byId.get(e.source)
      const b = byId.get(e.target)
      if (!a || !b) continue
      let dx = a.x - b.x
      let dy = a.y - b.y
      const dist = Math.hypot(dx, dy) || 0.01
      const force = (dist * dist) / k
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      a.vx -= fx
      a.vy -= fy
      b.vx += fx
      b.vy += fy
    }
    // 向心力 + 积分
    for (const s of simNodes) {
      if (s.fixed) {
        s.vx = 0
        s.vy = 0
        continue
      }
      s.vx += (cx - s.x) * 0.02
      s.vy += (cy - s.y) * 0.02
      s.vx *= damping
      s.vy *= damping
      const speed = Math.hypot(s.vx, s.vy)
      if (speed > temperature) {
        s.vx = (s.vx / speed) * temperature
        s.vy = (s.vy / speed) * temperature
      }
      s.x += s.vx
      s.y += s.vy
      // 边界约束
      const pad = 16
      s.x = Math.max(pad, Math.min(width - pad, s.x))
      s.y = Math.max(pad, Math.min(height - pad, s.y))
    }
    temperature *= cooling
  }

  return {
    nodes: simNodes,
    edges,
    tick,
    reheat() {
      temperature = Math.min(width, height) * 0.25
    },
    getNode(id: string) {
      return byId.get(id)
    },
    get temperature() {
      return temperature
    },
    set temperature(v: number) {
      temperature = v
    },
  } as ForceSim
}

/** 节点半径（按词频放大）。 */
export function nodeRadius(weight: number, maxWeight: number): number {
  const t = maxWeight > 0 ? weight / maxWeight : 0.5
  return 7 + Math.sqrt(t) * 12
}

/* ───────────────────────── 多智能体协作调用图（call graph） ─────────────────────────
 * 数据来自 Msg.agentDelegations（由 chat store 在 SSE 流式处理时结构化记录）。
 * 回答用户关心的三件事：① 谁发出（caller）② 调用了哪个 agent（callee）③ 是否出结果（status）。
 */

export type DelegRole = 'root' | 'coordinator' | 'executor' | 'unknown'
export type DelegStatus = AgentDelegation['status']

export interface AgentGraphNode {
  id: string // 智能体名（或 '主管(顶层)'）
  label: string
  role: DelegRole
  status: DelegStatus | 'idle'
  weight: number
}

export interface AgentGraphEdge {
  source: string
  target: string
  status: DelegStatus
  task: string
}

export interface AgentGraph {
  nodes: AgentGraphNode[]
  edges: AgentGraphEdge[]
}

/** 汇总一个会话里所有消息的委派关系。 */
export function sessionDelegations(messages: Msg[]): AgentDelegation[] {
  const out: AgentDelegation[] = []
  for (const m of messages) {
    const list = (m as { agentDelegations?: AgentDelegation[] }).agentDelegations
    if (list && list.length) out.push(...list)
  }
  return out
}

/**
 * 由扁平的委派记录构建调用图：
 *  - 节点 = 所有 caller + callee；
 *  - role：仅作 caller → root；既是 caller 又是 callee → coordinator；仅 callee → executor；
 *  - 节点 status：取该 agent 作为 callee 的最近一次委派状态；root 由其全部子委派是否完成推导；
 *  - 边 = 每条委派（caller → callee），按 (source,target) 去重，保留最近一次状态。
 */
export function buildAgentGraph(delegs: AgentDelegation[]): AgentGraph {
  if (!delegs.length) return { nodes: [], edges: [] }
  const callers = new Set<string>()
  const callees = new Set<string>()
  const outCount = new Map<string, number>()
  const inCount = new Map<string, number>()
  const lastByCallee = new Map<string, AgentDelegation>()
  const edgeMap = new Map<string, AgentDelegation>() // src|tgt → 最近一次

  for (const d of delegs) {
    callers.add(d.caller)
    callees.add(d.callee)
    outCount.set(d.caller, (outCount.get(d.caller) || 0) + 1)
    inCount.set(d.callee, (inCount.get(d.callee) || 0) + 1)
    lastByCallee.set(d.callee, d)
    edgeMap.set(`${d.caller}|${d.callee}`, d)
  }

  const names = new Set<string>([...callers, ...callees])
  const nodes: AgentGraphNode[] = [...names].map((id) => {
    const isCaller = callers.has(id)
    const isCallee = callees.has(id)
    let role: DelegRole = 'unknown'
    if (isCaller && !isCallee) role = 'root'
    else if (isCallee && isCaller) role = 'coordinator'
    else if (isCallee && !isCaller) role = 'executor'

    let status: DelegStatus | 'idle'
    const own = lastByCallee.get(id)
    if (own) {
      status = own.status
    } else {
      // root 节点本身不作为 callee，由其子委派完成情况推导
      const children = [...edgeMap.values()].filter((e) => e.caller === id)
      const anyRunning = children.some((e) => e.status === 'running')
      const allDone = children.length > 0 && children.every(
        (e) => e.status === 'done' || e.status === 'empty',
      )
      status = anyRunning ? 'running' : allDone ? 'done' : 'idle'
    }
    const weight = (outCount.get(id) || 0) + (inCount.get(id) || 0) + 1
    return { id, label: id, role, status, weight }
  })

  const edges: AgentGraphEdge[] = [...edgeMap.values()].map((e) => ({
    source: e.caller,
    target: e.callee,
    status: e.status,
    task: e.task,
  }))
  return { nodes, edges }
}

/** 把调用图转成力导向布局所需的节点/边形状（复用既有 createForceSimulation）。 */
export function agentGraphToSim(g: AgentGraph): { nodes: { id: string; weight: number }[]; edges: SimEdge[] } {
  return {
    nodes: g.nodes.map((n) => ({ id: n.id, weight: n.weight })),
    edges: g.edges.map((e) => ({ source: e.source, target: e.target, weight: 1 })),
  }
}

/**
 * 细胞气泡形式的委派流程图：把调用图映射成可被既有力导向引擎渲染的
 * ConceptGraph 形状，每个智能体是一个「细胞气泡」（圆点 + 脉冲膜），
 * 用角色配色与状态角标表达「主 agent 发出 → 管理 agent 委派 → 具体 agent → 结束」。
 *  - root 节点作为中心核（最大），coordinator 中层，executor 外层小泡；
 *  - 额外追加一个「结束」气泡（连回主 agent），形成完整流程闭环；
 *  - 节点 status 直接写入，由面板据角色/状态着色。
 */
export function buildDelegationCellGraph(delegs: AgentDelegation[]): ConceptGraph {
  const g = buildAgentGraph(delegs)
  const nodes: ConceptNode[] = g.nodes.map((n) => ({
    id: n.id,
    label: n.id,
    weight: (n.role === 'root' ? 5 : n.role === 'coordinator' ? 3 : 1.4) + n.weight * 0.3,
    kind: 'agent',
    role: n.role,
    status: n.status,
  }))
  const edges: ConceptEdge[] = g.edges.map((e) => ({
    source: e.source,
    target: e.target,
    weight: 1,
  }))
  // 补充「结束」气泡：连回主 agent，使「主→管理→具体→结束」流程闭环
  const root = g.nodes.find((n) => n.role === 'root')
  if (root) {
    const allDone =
      g.edges.length > 0 && g.edges.every((e) => e.status === 'done' || e.status === 'empty')
    const anyRunning = g.edges.some((e) => e.status === 'running')
    nodes.push({
      id: '__end__',
      label: '结束',
      weight: 4,
      kind: 'agent',
      role: 'root',
      status: allDone ? 'done' : anyRunning ? 'running' : 'idle',
      isEnd: true,
    })
    edges.push({ source: root.id, target: '__end__', weight: 1.2 })
  }
  return { nodes, edges }
}
