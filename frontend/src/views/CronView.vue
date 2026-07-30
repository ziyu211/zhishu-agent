<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import {
  NCard, NButton, NModal, NForm, NFormItem, NInput, NSelect, NSwitch,
  NInputNumber, NTag, NDrawer, NScrollbar, NEmpty, NSpace, NText,
} from 'naive-ui'
import { storeToRefs } from 'pinia'
import { useCronStore } from '@/stores/cron'
import { useAppStore } from '@/stores/app'
import type { CronJob } from '@/api/cron'

const cron = useCronStore()
const app = useAppStore()
/** 只读访客（仅 cron:read）隐藏全部写操作，避免点击后 403 */
const canWrite = computed(() => app.can('cron:write'))
const { jobs, loading, history } = storeToRefs(cron)
const message = useMessage()
const dialog = useDialog()

const showModal = ref(false)
const submitting = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({
  name: '',
  schedule_type: 'interval',
  every: 1,
  unit: 'hour',
  hour: 9,
  minute: 0,
  expr: '0 9 * * *',
  action: 'chat',
  payload: '',
  model: '',
  enabled: true,
})

const typeOptions = [
  { label: '间隔(interval)', value: 'interval' },
  { label: '每日(daily)', value: 'daily' },
  { label: 'Cron 表达式', value: 'cron' },
]
const unitOptions = [
  { label: '秒', value: 'second' },
  { label: '分钟', value: 'minute' },
  { label: '小时', value: 'hour' },
  { label: '天', value: 'day' },
]
const actionOptions = [
  { label: '对话(chat)', value: 'chat' },
  { label: '命令(shell)', value: 'shell' },
]

function buildConfig() {
  if (form.schedule_type === 'interval') return { every: form.every, unit: form.unit }
  if (form.schedule_type === 'daily') return { hour: form.hour, minute: form.minute }
  return { expr: form.expr }
}

function resetForm() {
  Object.assign(form, {
    name: '', schedule_type: 'interval', every: 1, unit: 'hour', hour: 9, minute: 0,
    expr: '0 9 * * *', action: 'chat', payload: '', model: '', enabled: true,
  })
}

function openCreate() {
  editingId.value = null
  resetForm()
  showModal.value = true
}

function openEdit(it: CronJob) {
  editingId.value = it.id
  form.name = it.name
  form.schedule_type = it.schedule_type
  form.action = it.action
  form.payload = it.payload
  form.model = it.model || ''
  form.enabled = it.enabled
  const c = it.schedule_config || {}
  if (it.schedule_type === 'interval') { form.every = c.every ?? 1; form.unit = c.unit ?? 'hour' }
  else if (it.schedule_type === 'daily') { form.hour = c.hour ?? 9; form.minute = c.minute ?? 0 }
  else { form.expr = c.expr ?? '0 9 * * *' }
  showModal.value = true
}

async function submit() {
  if (!form.name.trim()) return message.error('请填写任务名称')
  if (!form.payload.trim()) return message.error('请填写提示词或命令')
  submitting.value = true
  const body = {
    name: form.name.trim(),
    schedule_type: form.schedule_type,
    schedule_config: buildConfig(),
    action: form.action,
    payload: form.payload,
    model: form.model || null,
    enabled: form.enabled,
  }
  try {
    if (editingId.value) await cron.update(editingId.value, body)
    else await cron.create(body)
    showModal.value = false
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

function confirmDelete(it: CronJob) {
  dialog.warning({
    title: '删除定时任务',
    content: `确认删除「${it.name}」？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => cron.remove(it.id),
  })
}

async function handleRun(it: CronJob) {
  const out = await cron.runNow(it.id)
  dialog.success({ title: `执行结果：${it.name}`, content: out || '（无输出）', style: { whiteSpace: 'pre-wrap' } })
}

const showHistory = ref(false)
async function openHistory(it: CronJob) {
  await cron.loadHistory(it.id)
  showHistory.value = true
}

const scheduleText = (it: CronJob) => {
  const c = it.schedule_config || {}
  if (it.schedule_type === 'interval') return `每 ${c.every} ${c.unit}`
  if (it.schedule_type === 'daily') return `每天 ${c.hour}:${String(c.minute).padStart(2, '0')}`
  return c.expr || 'cron'
}

onMounted(() => cron.load())
</script>

<template>
  <div class="cron-view">
    <NCard :bordered="false" class="page-card">
      <div class="page-header">
        <div>
          <h2 class="page-title">定时任务</h2>
          <p class="page-sub">内网合规版调度器：周期性跑对话任务或沙箱命令（对标 Hermes cron）。</p>
        </div>
        <NButton v-if="canWrite" type="primary" @click="openCreate">+ 新建任务</NButton>
        <NTag v-else size="small" :bordered="false">只读模式</NTag>
      </div>

      <NSpace v-if="!loading && jobs.length === 0" vertical align="center" style="padding: 40px 0">
        <NEmpty :description="canWrite ? '暂无定时任务，点击右上角新建' : '暂无可见的定时任务'" />
      </NSpace>

      <div v-else class="job-table">
        <div class="job-row job-head">
          <span>名称</span><span>调度</span><span>动作</span><span>下次触发</span><span>启用</span><span>操作</span>
        </div>
        <div v-for="it in jobs" :key="it.id" class="job-row">
          <span class="job-name">{{ it.name }}</span>
          <span><NTag size="small" :bordered="false">{{ scheduleText(it) }}</NTag></span>
          <span>
            <NTag size="small" :type="it.action === 'chat' ? 'info' : 'warning'" :bordered="false">
              {{ it.action }}
            </NTag>
          </span>
          <span class="muted">{{ it.next_run || '—' }}</span>
          <span><NSwitch :value="it.enabled" :disabled="!canWrite" @update:value="(v: boolean) => cron.toggle(it.id, v)" /></span>
          <span class="job-actions">
            <NButton v-if="canWrite" size="small" tertiary @click="handleRun(it)">执行</NButton>
            <NButton size="small" tertiary @click="openHistory(it)">历史</NButton>
            <NButton v-if="canWrite" size="small" tertiary @click="openEdit(it)">编辑</NButton>
            <NButton v-if="canWrite" size="small" tertiary type="error" @click="confirmDelete(it)">删除</NButton>
          </span>
        </div>
      </div>
    </NCard>

    <!-- 新建/编辑 -->
    <NModal v-model:show="showModal" :title="editingId ? '编辑任务' : '新建定时任务'" preset="card" style="width: 560px">
      <NForm label-placement="top">
        <NFormItem label="任务名称">
          <NInput v-model:value="form.name" placeholder="如：每日安全巡检" />
        </NFormItem>
        <NFormItem label="调度类型">
          <NSelect v-model:value="form.schedule_type" :options="typeOptions" />
        </NFormItem>
        <NFormItem v-if="form.schedule_type === 'interval'" label="间隔">
          <NInputNumber v-model:value="form.every" :min="1" style="width: 120px" />
          <NSelect v-model:value="form.unit" :options="unitOptions" style="width: 140px; margin-left: 8px" />
        </NFormItem>
        <NFormItem v-else-if="form.schedule_type === 'daily'" label="每日触发时间">
          <NInputNumber v-model:value="form.hour" :min="0" :max="23" style="width: 100px" /> 时
          <NInputNumber v-model:value="form.minute" :min="0" :max="59" style="width: 100px; margin-left: 8px" /> 分
        </NFormItem>
        <NFormItem v-else label="Cron 表达式">
          <NInput v-model:value="form.expr" placeholder="0 9 * * *（分 时 日 月 周）" />
        </NFormItem>
        <NFormItem label="动作类型">
          <NSelect v-model:value="form.action" :options="actionOptions" />
        </NFormItem>
        <NFormItem :label="form.action === 'chat' ? '提示词' : '命令（沙箱内执行）'">
          <NInput v-model:value="form.payload" type="textarea" :rows="4"
                  :placeholder="form.action === 'chat' ? '让智能体定期执行的任务描述' : '如：ls -la /data'" />
        </NFormItem>
        <NFormItem v-if="form.action === 'chat'" label="指定模型（可选，留空用默认）">
          <NInput v-model:value="form.model" placeholder="如 ollama/qwen2.5:7b" />
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="form.enabled" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" :loading="submitting" @click="submit">保存</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 历史 -->
    <NDrawer v-model:show="showHistory" :width="480" placement="right" title="执行历史">
      <NScrollbar style="max-height: 100%">
        <div style="padding: 16px">
          <NEmpty v-if="history.length === 0" description="暂无执行记录" />
          <div v-for="h in history" :key="h.id" class="hist-item">
            <div class="hist-meta">
              <NTag size="small" :type="h.status === 'success' ? 'success' : 'error'" :bordered="false">{{ h.status }}</NTag>
              <NText depth="3" style="font-size: 12px">{{ h.started_at }}</NText>
            </div>
            <pre class="hist-out">{{ h.output || '（无输出）' }}</pre>
          </div>
        </div>
      </NScrollbar>
    </NDrawer>
  </div>
</template>

<style scoped lang="scss">
.cron-view { padding: 8px 4px; }
.page-card { border-radius: 12px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.page-title { font-size: 20px; font-weight: 600; margin: 0; }
.page-sub { color: #888; font-size: 13px; margin: 4px 0 0; }
.job-table { display: flex; flex-direction: column; }
.job-row {
  display: grid; grid-template-columns: 1.4fr 1.2fr 0.8fr 1.4fr 0.7fr 2fr;
  align-items: center; gap: 8px; padding: 10px 8px; border-bottom: 1px solid #f0f0f0;
}
.job-head { font-size: 12px; color: #999; font-weight: 600; }
.job-name { font-weight: 500; }
.muted { color: #999; font-size: 13px; }
.job-actions { display: flex; gap: 4px; flex-wrap: wrap; }
.hist-item { padding: 10px 0; border-bottom: 1px solid #f0f0f0; }
.hist-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.hist-out { background: #f7f7f8; border-radius: 6px; padding: 8px; font-size: 12px; white-space: pre-wrap; max-height: 200px; overflow: auto; }
</style>
