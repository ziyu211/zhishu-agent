<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NButton, NTag, useMessage } from 'naive-ui'
import { api } from '@/api/client'

const message = useMessage()
const loading = ref(false)
const status = ref<any>(null)
const records = ref<any[]>([])

const redactInput = ref('张三 手机 13800138000 邮箱 zhang@corp.com 身份证 110101199003071234 卡号 6222021234567890')
const redactResult = ref('')
const redactLoading = ref(false)
async function runRedact() {
  if (!redactInput.value.trim()) return
  redactLoading.value = true
  try {
    const r = await api.adminRedact(redactInput.value)
    redactResult.value = r.enabled ? r.result : '（脱敏未启用）' + (r.result || '')
  } catch (e: any) {
    message.error(e?.message || '脱敏自测失败')
  } finally {
    redactLoading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const [s, a] = await Promise.all([api.adminStatus(), api.adminAudit(200)])
    status.value = s
    records.value = a.records || []
  } catch (e: any) {
    message.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function badge(v: boolean) {
  return v ? { type: 'success', text: '已启用' } : { type: 'default', text: '已关闭' }
}

onMounted(load)
</script>

<template>
  <div class="system-view">
    <header class="page-header">
      <div>
        <div class="header-title">系统</div>
        <div class="header-sub">运行状态、安全策略与审计日志</div>
      </div>
      <NButton size="small" :loading="loading" quaternary @click="load">刷新</NButton>
    </header>

    <div class="system-content">
      <section v-if="status" class="card-block">
        <h3 class="block-title">运行状态</h3>
        <div class="stat-grid">
          <div class="stat"><span class="stat-label">鉴权</span><NTag size="tiny" :type="badge(status.auth_enabled).type" :bordered="false">{{ badge(status.auth_enabled).text }}</NTag></div>
          <div class="stat"><span class="stat-label">国密 SM</span><NTag size="tiny" :type="badge(status.sm_enabled).type" :bordered="false">{{ badge(status.sm_enabled).text }}</NTag></div>
          <div class="stat"><span class="stat-label">审计日志</span><NTag size="tiny" :type="badge(status.audit_enabled).type" :bordered="false">{{ badge(status.audit_enabled).text }}</NTag></div>
          <div class="stat"><span class="stat-label">出网</span><NTag size="tiny" :type="status.outbound_allowed ? 'default' : 'success'" :bordered="false">{{ status.outbound_allowed ? '允许' : '已隔离' }}</NTag></div>
          <div class="stat"><span class="stat-label">默认模型</span><span class="stat-val mono">{{ status.default_model || '—' }}</span></div>
          <div class="stat"><span class="stat-label">知识库</span><span class="stat-val mono">{{ status.knowledge_base?.vectors ?? '—' }} 向量</span></div>
        </div>
        <div class="chips">
          <div class="chip-group"><span class="chip-label">Provider</span><span v-for="p in status.providers" :key="p" class="chip mono">{{ p }}</span></div>
          <div class="chip-group"><span class="chip-label">工具</span><span v-for="t in status.tools" :key="t" class="chip mono">{{ t }}</span></div>
        </div>
      </section>

      <section class="card-block">
        <h3 class="block-title">数据脱敏自测</h3>
        <p class="block-hint">验证 PII 脱敏（手机号 / 邮箱 / 身份证 / 银行卡等）是否在落库与输出前被正则遮蔽。仅本地计算，不会上传。</p>
        <div class="redact-box">
          <textarea v-model="redactInput" class="redact-input" rows="3" placeholder="输入含敏感信息的文本"></textarea>
          <NButton size="small" :loading="redactLoading" type="primary" @click="runRedact">运行脱敏</NButton>
        </div>
        <div v-if="redactResult" class="redact-out mono">{{ redactResult }}</div>
      </section>

      <section class="card-block">
        <h3 class="block-title">审计日志（最近 {{ records.length }} 条）</h3>
        <div v-if="!records.length" class="hint-empty">暂无审计记录</div>
        <table v-else class="audit-table">
          <thead><tr><th>时间</th><th>用户</th><th>动作</th><th>详情</th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in records" :key="i">
              <td class="mono muted">{{ r.ts }}</td>
              <td class="mono">{{ r.user || '—' }}</td>
              <td><NTag size="tiny" :bordered="false">{{ r.action }}</NTag></td>
              <td class="detail">{{ r.detail || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.system-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.system-content { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 20px; }
.card-block { background: $bg-card; border: 1px solid $border-color; border-radius: $radius-md; padding: 18px; }
.block-title { font-size: 14px; font-weight: 600; color: $text-primary; margin: 0 0 14px; }

.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.stat { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; background: $bg-secondary; border-radius: $radius-sm; }
.stat-label { font-size: 12px; color: $text-muted; }
.stat-val { font-size: 12px; color: $text-primary; }

.chips { margin-top: 14px; display: flex; flex-direction: column; gap: 10px; }
.chip-group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.chip-label { font-size: 12px; color: $text-muted; width: 64px; flex-shrink: 0; }
.block-hint { font-size: 12px; color: $text-muted; margin: 0 0 12px; }
.redact-box { display: flex; flex-direction: column; gap: 10px; }
.redact-input { width: 100%; resize: vertical; border: 1px solid $border-color; border-radius: $radius-sm;
  padding: 10px 12px; font-size: 13px; font-family: $font-code; background: $bg-secondary; color: $text-primary; }
.redact-out { margin-top: 10px; padding: 10px 12px; background: $bg-secondary; border: 1px solid $border-light;
  border-radius: $radius-sm; font-size: 13px; color: $text-primary; word-break: break-all; }
.chip { font-size: 12px; padding: 2px 8px; border-radius: 6px; background: $bg-secondary; color: $text-secondary; border: 1px solid $border-light; }

.audit-table { width: 100%; border-collapse: collapse; font-size: 13px;
  th { text-align: left; font-weight: 600; color: $text-muted; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid $border-color; }
  td { padding: 8px 10px; border-bottom: 1px solid $border-light; color: $text-secondary; vertical-align: top; }
  .detail { word-break: break-all; }
}
.mono { font-family: $font-code; }
.muted { color: $text-muted; }
.hint-empty { color: $text-muted; font-size: 13px; padding: 16px 0; text-align: center; }
</style>
