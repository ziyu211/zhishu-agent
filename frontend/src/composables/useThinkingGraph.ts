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
import type { Msg } from '@/stores/chat'

/* ───────────────────────── 推理步骤图 ───────────────────────── */

export type StepKind = 'goal' | 'observe' | 'analyze' | 'plan' | 'action' | 'conclude' | 'note'

export interface ThinkingStep {
  id: string
  index: number
  text: string
  kind: StepKind
  /** 编号层级（如 "1.2.3" → 2），用于缩进展示 */
  depth: number
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
