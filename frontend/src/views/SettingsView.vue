<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { NButton, NInput, NSelect, NCard, NSwitch, NInputNumber, useMessage } from 'naive-ui'
import { api } from '@/api/client'
import { useTheme } from '@/composables/useTheme'
import { useAppStore } from '@/stores/app'

const message = useMessage()
const { isDark, toggle } = useTheme()
const app = useAppStore()

const oldPwd = ref('')
const newPwd = ref('')
const confirmPwd = ref('')
const modelOptions = ref<any[]>([])
const selectedDefault = ref('')
// 长期记忆（admin 自助开关）
const vectorEnabled = ref(false)
const vectorTopK = ref(5)
const memoryLoading = ref(false)

// 安全与网络（admin 自助开关，运行时即时生效）
const security = reactive({
  allow_private_fetch: false,
  outbound_allow: false,
  allow_code_exec: true,
  allow_shell: true,
  shell_enforce_allowlist: true,
  enable_audit: true,
  enable_redact: true,
  code_exec_network_isolated: false,
})
const securityLoading = ref(false)
const securityFields: { key: keyof typeof security; label: string; desc: string }[] = [
  { key: 'allow_private_fetch', label: '内网模型探测', desc: '允许从内网/私有地址（如本地 Ollama/vLLM）拉取模型列表' },
  { key: 'outbound_allow', label: '工具出网', desc: '允许联网工具（网页搜索、外部 API 调用）访问公网' },
  { key: 'allow_code_exec', label: '代码执行', desc: '允许智能体生成并执行 Python 代码片段（沙箱隔离）' },
  { key: 'code_exec_network_isolated', label: '代码执行出网隔离', desc: '开启后 code_exec 沙箱禁止访问外部网络（与全局工具出网解耦，默认关闭=允许出网）' },
  { key: 'allow_shell', label: 'Shell 执行', desc: '允许定时任务与 terminal_run 工具执行 Shell 命令' },
  { key: 'shell_enforce_allowlist', label: 'Shell 白名单', desc: '强制 Shell 命令可执行文件白名单（关闭仅保留高危拒绝清单）' },
  { key: 'enable_audit', label: '审计日志', desc: '记录关键操作审计日志（落库 zhishu_audit.db）' },
  { key: 'enable_redact', label: '数据脱敏', desc: '对落库/输出中的 PII（手机号、身份证等）自动遮蔽' },
]

async function loadModels() {
  try {
    const r = await api.listModels()
    const opts: any[] = []
    for (const p of r.providers || []) {
      for (const m of p.models || []) opts.push({ label: `${p.label} / ${m}`, value: `${p.provider}/${m}` })
    }
    modelOptions.value = opts
    selectedDefault.value = r.default_model || ''
  } catch { /* 静默 */ }
}

function changePassword() {
  if (!oldPwd.value) {
    message.error('请输入原密码')
    return
  }
  if (newPwd.value.length < 6) {
    message.error('新密码至少 6 位')
    return
  }
  if (newPwd.value !== confirmPwd.value) {
    message.error('两次输入的新密码不一致')
    return
  }
  api.changePassword({ old_password: oldPwd.value, new_password: newPwd.value })
    .then(() => { message.success('密码已修改，请使用新密码重新登录'); oldPwd.value = ''; newPwd.value = ''; confirmPwd.value = '' })
    .catch((e: any) => message.error(e?.message || '修改失败'))
}
function setDefault() {
  if (!selectedDefault.value) return
  api.setDefaultModel(selectedDefault.value)
    .then(() => message.success('默认模型已更新'))
    .catch((e: any) => message.error(e?.message || '操作失败'))
}

async function loadMemorySettings() {
  try {
    const r = await api.getSettings()
    vectorEnabled.value = !!r.memory?.vector_enabled
    vectorTopK.value = r.memory?.vector_top_k ?? 5
  } catch { /* 静默 */ }
}
function saveMemorySettings() {
  memoryLoading.value = true
  api.updateSettings({
    memory: { vector_enabled: vectorEnabled.value, vector_top_k: vectorTopK.value },
  })
    .then((r) => {
      vectorEnabled.value = !!r.memory?.vector_enabled
      vectorTopK.value = r.memory?.vector_top_k ?? vectorTopK.value
      message.success('长期记忆设置已保存')
    })
    .catch((e: any) => message.error(e?.message || '保存失败'))
    .finally(() => { memoryLoading.value = false })
}

async function loadSecuritySettings() {
  try {
    const r = await api.getSettings()
    if (r.security) Object.assign(security, r.security)
  } catch { /* 静默 */ }
}
function saveSecuritySettings() {
  securityLoading.value = true
  api.updateSettings({ security: { ...security } })
    .then((r) => {
      if (r.security) Object.assign(security, r.security)
      message.success('安全与网络设置已保存并即时生效')
    })
    .catch((e: any) => message.error(e?.message || '保存失败'))
    .finally(() => { securityLoading.value = false })
}

onMounted(() => {
  loadModels()
  if (app.isAdmin) {
    loadMemorySettings()
    loadSecuritySettings()
  }
})
</script>

<template>
  <div class="settings-view">
    <header class="page-header">
      <div>
        <div class="header-title">设置</div>
        <div class="header-sub">账户、默认模型与外观</div>
      </div>
    </header>

    <div class="settings-content">
      <section class="card-block">
        <h3 class="block-title">账户</h3>
        <div class="form-row">
          <label>当前用户</label>
          <span class="val">{{ app.user?.user }}（{{ app.user?.role_label || app.user?.role }}）</span>
        </div>
        <div class="form-row">
          <label>修改密码</label>
          <div class="inline-form">
            <NInput v-model:value="oldPwd" type="password" placeholder="原密码" size="small" />
            <NInput v-model:value="newPwd" type="password" placeholder="新密码（≥6 位）" size="small" />
            <NInput v-model:value="confirmPwd" type="password" placeholder="确认新密码" size="small" />
            <NButton size="small" @click="changePassword">保存</NButton>
          </div>
        </div>
      </section>

      <section class="card-block">
        <h3 class="block-title">模型</h3>
        <div class="form-row">
          <label>默认模型</label>
          <div class="inline-form">
            <NSelect v-model:value="selectedDefault" :options="modelOptions" placeholder="选择默认模型" size="small" style="min-width: 260px" />
            <NButton size="small" :disabled="!app.can('models:write')" @click="setDefault">应用</NButton>
          </div>
        </div>
      </section>

      <section class="card-block" v-if="app.isAdmin">
        <h3 class="block-title">长期记忆</h3>
        <div class="form-row">
          <label>向量记忆</label>
          <div class="inline-form">
            <NSwitch v-model:value="vectorEnabled" />
            <span class="val">跨会话语义召回（需配置可用的 Embedding 模型，否则自动关闭）</span>
          </div>
        </div>
        <div class="form-row">
          <label>召回条数</label>
          <div class="inline-form">
            <NInputNumber v-model:value="vectorTopK" :min="1" :max="20" size="small" style="width: 110px" />
            <NButton size="small" :loading="memoryLoading" @click="saveMemorySettings">保存</NButton>
          </div>
        </div>
      </section>

      <section class="card-block" v-if="app.isAdmin">
        <h3 class="block-title">安全与网络</h3>
        <div class="form-row" v-for="f in securityFields" :key="f.key">
          <label>{{ f.label }}</label>
          <div class="inline-form">
            <NSwitch v-model:value="security[f.key]" />
            <span class="val">{{ f.desc }}</span>
          </div>
        </div>
        <div class="form-row">
          <label></label>
          <div class="inline-form">
            <NButton size="small" :loading="securityLoading" @click="saveSecuritySettings">保存安全设置</NButton>
          </div>
        </div>
      </section>

      <section class="card-block">
        <h3 class="block-title">外观</h3>
        <div class="form-row">
          <label>主题</label>
          <div class="inline-form">
            <span class="val">{{ isDark ? '暗色' : '亮色' }}</span>
            <NButton size="small" quaternary @click="toggle">
              <template #icon>
                <svg v-if="isDark" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
                <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
              </template>
              切换
            </NButton>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.settings-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.settings-content { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 20px; max-width: 760px; }
.card-block { background: $bg-card; border: 1px solid $border-color; border-radius: $radius-md; padding: 18px; }
.block-title { font-size: 14px; font-weight: 600; color: $text-primary; margin: 0 0 14px; }
.form-row { display: flex; align-items: center; gap: 16px; padding: 8px 0; }
.form-row label { width: 80px; flex-shrink: 0; font-size: 13px; color: $text-secondary; }
.val { font-size: 13px; color: $text-primary; }
.inline-form { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
