/**
 * 渲染冒烟测试：真实执行组件 setup 并 SSR 渲染，确保不抛运行时异常。
 *
 * 目的：TDZ / 空引用这类错误只在「setup 实际执行」时才暴露，静态分析和 vite build
 * 都发现不了。本脚本对思维图谱面板的三条关键分支各渲染一次：
 *   ① 空会话        ② 多智能体委派（细胞气泡图）  ③ 仅有推理链（自动切到推理步骤 Tab）
 * 任一分支抛错即判定失败。
 */
import { createSSRApp, h, defineComponent, computed } from 'vue'
import { renderToString } from '@vue/server-renderer'
import ThinkingGraphPanel from '@/components/chat/ThinkingGraphPanel.vue'
import type { Msg, AgentDelegation, AgentTraceStep } from '@/stores/chat'

const delegations: AgentDelegation[] = [
  { id: 'd1', caller: '主管(顶层)', callee: 'Orchestrator', task: '统筹股票分析', result: '已完成', status: 'done', startedAt: 1, endedAt: 2 },
  { id: 'd2', caller: 'Orchestrator', callee: 'Research', task: '行业研究', result: '行业报告', status: 'done', startedAt: 2, endedAt: 3 },
  { id: 'd3', caller: 'Orchestrator', callee: 'Factor', task: '因子挖掘', result: '', status: 'running', startedAt: 3 },
  { id: 'd4', caller: 'Orchestrator', callee: 'Risk', task: '风险评估', result: '', status: 'error', startedAt: 3, endedAt: 4 },
]

const trace: AgentTraceStep[] = [
  { id: 't1', index: 1, text: '明确目标：分析该股票', kind: 'goal', depth: 0 },
  { id: 't2', index: 2, text: '调用行业研究员', kind: 'action', depth: 1, agent: 'Research' },
  { id: 't3', index: 3, text: '汇总结论', kind: 'conclude', depth: 0 },
]

const cases: Array<{ name: string; messages: Msg[] }> = [
  { name: '空会话', messages: [] },
  {
    name: '多智能体委派（细胞气泡图）',
    messages: [
      { id: 'u1', role: 'user', content: '帮我建一个股票分析团队', ts: 1 } as Msg,
      { id: 'a1', role: 'assistant', content: '已创建团队', ts: 2, agentDelegations: delegations, agentTrace: trace } as Msg,
    ],
  },
  {
    name: '仅推理链（应自动切到推理步骤 Tab）',
    messages: [
      { id: 'u2', role: 'user', content: '解释一下量化因子', ts: 1 } as Msg,
      { id: 'a2', role: 'assistant', content: '因子是…', ts: 2, agentTrace: trace } as Msg,
    ],
  },
  {
    name: '模型思考文本（概念图谱）',
    messages: [
      { id: 'u3', role: 'user', content: '什么是动量因子', ts: 1 } as Msg,
      { id: 'a3', role: 'assistant', content: '动量因子是…', ts: 2, reasoning: '首先分析动量的定义，然后考察收益率的持续性，最后给出结论。' } as Msg,
    ],
  },
]

/** 自检探针：一个必然触发 TDZ 的组件。若它没有抛错，说明本测试根本抓不到 TDZ，
 *  结果不可信（防止"永远 PASS"的假绿）。 */
const TdzProbe = defineComponent({
  setup() {
    // @ts-expect-error 故意在声明前使用，用于验证检测能力
    if (probe.value) {
      /* noop */
    }
    // eslint-disable-next-line no-unreachable
    const probe = computed(() => 1)
    return () => h('div', String(probe.value))
  },
})

async function selfCheck(): Promise<boolean> {
  try {
    await renderToString(createSSRApp({ render: () => h(TdzProbe) }))
    console.log('[自检失败] TDZ 探针未抛异常 —— 本测试无法捕获 TDZ，结果不可信')
    return false
  } catch (e: any) {
    console.log(`[自检通过] TDZ 探针如期抛出 ${e?.name}，测试具备检测能力`)
    return true
  }
}

async function main() {
  let failed = 0
  if (!(await selfCheck())) process.exit(1)
  for (const c of cases) {
    try {
      const app = createSSRApp({
        render: () => h(ThinkingGraphPanel as any, { messages: c.messages, onClose: () => {}, onFocusMessage: () => {} }),
      })
      const html = await renderToString(app)
      if (!html || html.length < 20) {
        console.log(`[FAIL] ${c.name}：渲染结果为空`)
        failed++
      } else {
        const hasPanel = html.includes('tg-panel')
        console.log(`[${hasPanel ? 'PASS' : 'FAIL'}] ${c.name}：渲染 ${html.length} 字节${hasPanel ? '，面板根节点存在' : '，缺少 tg-panel 根节点'}`)
        if (!hasPanel) failed++
      }
    } catch (e: any) {
      console.log(`[FAIL] ${c.name}：抛出异常 → ${e?.name}: ${e?.message}`)
      failed++
    }
  }
  console.log(failed === 0 ? '\nRENDER_CHECK_PASS' : `\nRENDER_CHECK_FAIL：${failed} 个用例失败`)
  process.exit(failed === 0 ? 0 : 1)
}

main()
