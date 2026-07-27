<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { NModal, NForm, NFormItem, NInput, NSwitch, NButton } from 'naive-ui'
import { api } from '@/api/client'
import ModuleList from '@/components/modules/ModuleList.vue'

const message = useMessage()
const loading = ref(false)
const skills = ref<any[]>([])

const showModal = ref(false)
const submitting = ref(false)
const editing = ref<string | null>(null)
const form = reactive<{ name: string; description: string; version: string; content: string; enabled: boolean }>({
  name: '',
  description: '',
  version: '1.0.0',
  content: '',
  enabled: true,
})

async function load() {
  loading.value = true
  try {
    const d = await api.listSkills()
    skills.value = d.skills || []
  } catch (e: any) {
    message.error(e?.message || '加载技能失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', description: '', version: '1.0.0', content: '', enabled: true })
  showModal.value = true
}

async function openEdit(it: any) {
  try {
    const d = await api.getSkill(it.name)
    editing.value = it.name
    Object.assign(form, {
      name: d.name,
      description: d.description || '',
      version: d.version || '1.0.0',
      content: d.content || '',
      enabled: d.enabled !== false,
    })
    showModal.value = true
  } catch (e: any) {
    message.error(e?.message || '读取技能失败')
  }
}

async function submit() {
  if (!form.name.trim()) {
    message.warning('请填写技能名称')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.updateSkill(editing.value, {
        description: form.description,
        version: form.version,
        content: form.content,
        enabled: form.enabled,
      })
      message.success('已更新')
    } else {
      await api.createSkill({
        name: form.name.trim(),
        description: form.description,
        version: form.version,
        content: form.content,
        enabled: form.enabled,
      })
      message.success('已创建')
    }
    showModal.value = false
    await load()
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function remove(it: any) {
  try {
    await api.deleteSkill(it.name)
    message.success('已删除')
    await load()
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function onToggle(p: { name: string; enabled: boolean }) {
  try {
    await api.toggleSkill(p.name, p.enabled)
    const it = skills.value.find((s) => s.name === p.name)
    if (it) it.enabled = p.enabled
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  }
}

onMounted(load)
</script>

<template>
  <div class="module-view">
    <header class="page-header">
      <div>
        <div class="header-title">技能</div>
        <div class="header-sub">技能是一段注入 Agent 系统提示的指令（Markdown）。启用后，Agent 在每次对话都会参考这些指令。</div>
      </div>
      <NButton type="primary" size="small" @click="openCreate">
        <template #icon>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </template>
        新建技能
      </NButton>
    </header>

    <div class="module-content">
      <ModuleList
        title="技能"
        :items="skills"
        :loading="loading"
        :editable="true"
        empty-text="暂无技能，点击右上角「新建技能」"
        search-placeholder="搜索技能..."
        @toggle="onToggle"
        @refresh="load"
        @edit="openEdit"
        @delete="remove"
      />
    </div>

    <NModal v-model:show="showModal" :title="editing ? '编辑技能' : '新建技能'" preset="card" style="width: 640px; max-width: 92vw;">
      <NForm>
        <NFormItem label="名称">
          <NInput v-model:value="form.name" :disabled="!!editing" placeholder="技能目录名（英文/数字/.-_）" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="form.description" placeholder="一句话描述这个技能的用途" />
        </NFormItem>
        <NFormItem label="版本">
          <NInput v-model:value="form.version" placeholder="1.0.0" />
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="form.enabled" />
        </NFormItem>
        <NFormItem label="指令内容（注入 Agent）">
          <NInput
            v-model:value="form.content"
            type="textarea"
            :autosize="{ minRows: 6, maxRows: 18 }"
            placeholder="例如：当用户询问天气时，先确认城市，再调用工具查询。"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <div class="modal-footer">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" :loading="submitting" @click="submit">保存</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.module-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.module-content { flex: 1; overflow-y: auto; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; }
</style>
