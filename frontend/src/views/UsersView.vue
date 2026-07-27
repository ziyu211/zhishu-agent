<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import {
  NButton, NModal, NForm, NFormItem, NInput, NSelect, NTag, useMessage, useDialog,
  NCard, NSpace, NPagination, NCheckbox, NTooltip, NPopconfirm, NInputGroup,
  NEmpty, NSpin,
} from 'naive-ui'
import { api } from '@/api/client'

const message = useMessage()
const dialog = useDialog()

const users = ref<any[]>([])
const roles = ref<any[]>([])
const loading = ref(false)

const showCreate = ref(false)
const newUser = ref({ username: '', password: '', role: 'user', display_name: '' })

const showEdit = ref(false)
const editUser = ref<any>(null)
const editForm = ref({ display_name: '', role: 'user', status: 'active' })

const showReset = ref(false)
const resetId = ref<number | null>(null)
const resetUsername = ref('')
const resetPwd = ref('')

const search = ref('')
const filterRole = ref<string | null>(null)
const filterStatus = ref<string | null>(null)
const sortKey = ref<'id' | 'username' | 'created_at' | 'last_login'>('id')
const sortAsc = ref(true)

const page = ref(1)
const pageSize = ref(10)
const selectedIds = ref<number[]>([])

const roleOptions = computed(() => [
  { label: '全部角色', value: null },
  ...roles.value.map((x) => ({ label: `${x.label}（${x.value}）`, value: x.value })),
])
const statusOptions = [
  { label: '全部状态', value: null },
  { label: '启用', value: 'active' },
  { label: '停用', value: 'disabled' },
]
const rolePlainOptions = computed(() =>
  roles.value.map((x) => ({ label: `${x.label}（${x.value}）`, value: x.value })),
)

const stats = computed(() => {
  const total = users.value.length
  const active = users.value.filter((u) => u.status === 'active').length
  const disabled = total - active
  const admins = users.value.filter((u) => u.role === 'admin').length
  return { total, active, disabled, admins }
})

const filteredUsers = computed(() => {
  let list = users.value.slice()
  const q = search.value.trim().toLowerCase()
  if (q) {
    list = list.filter(
      (u) =>
        (u.username || '').toLowerCase().includes(q) ||
        (u.display_name || '').toLowerCase().includes(q) ||
        String(u.id).includes(q),
    )
  }
  if (filterRole.value) list = list.filter((u) => u.role === filterRole.value)
  if (filterStatus.value) list = list.filter((u) => u.status === filterStatus.value)

  list.sort((a, b) => {
    const ak = a[sortKey.value]
    const bk = b[sortKey.value]
    if (ak === bk) return 0
    if (ak == null) return sortAsc.value ? -1 : 1
    if (bk == null) return sortAsc.value ? 1 : -1
    const r = ak > bk ? 1 : -1
    return sortAsc.value ? r : -r
  })
  return list
})

const totalFiltered = computed(() => filteredUsers.value.length)
const pagedUsers = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredUsers.value.slice(start, start + pageSize.value)
})

watch([search, filterRole, filterStatus], () => {
  page.value = 1
})

function roleLabel(r: string) {
  return roles.value.find((x) => x.value === r)?.label || r
}
function rolePerms(r: string) {
  return roles.value.find((x) => x.value === r)?.perms || []
}
function roleColor(r: string) {
  if (r === 'admin') return 'error'
  if (r === 'operator') return 'warning'
  if (r === 'viewer') return 'default'
  return 'success'
}

async function load() {
  loading.value = true
  try {
    const [u, r] = await Promise.all([api.listUsers(), api.listRoles()])
    users.value = u.users || []
    roles.value = r.roles || []
    selectedIds.value = []
  } catch (e: any) {
    message.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function formatDate(s?: string) {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  return d.toLocaleString('zh-CN', { hour12: false })
}

function toggleSort(key: 'id' | 'username' | 'created_at' | 'last_login') {
  if (sortKey.value === key) sortAsc.value = !sortAsc.value
  else {
    sortKey.value = key
    sortAsc.value = true
  }
}

function pwdStrength(pwd: string): { score: number; text: string; color: string } {
  let score = 0
  if (!pwd) return { score: 0, text: '', color: 'default' }
  if (pwd.length >= 8) score += 1
  if (pwd.length >= 12) score += 1
  if (/[A-Z]/.test(pwd)) score += 1
  if (/[a-z]/.test(pwd)) score += 1
  if (/\d/.test(pwd)) score += 1
  if (/[^A-Za-z0-9]/.test(pwd)) score += 1
  if (score <= 2) return { score, text: '弱', color: 'error' }
  if (score <= 4) return { score, text: '中', color: 'warning' }
  return { score, text: '强', color: 'success' }
}
const createPwd = computed(() => pwdStrength(newUser.value.password))
const resetPwdStrength = computed(() => pwdStrength(resetPwd.value))

function openCreate() {
  newUser.value = { username: '', password: '', role: 'user', display_name: '' }
  showCreate.value = true
}
async function submitCreate() {
  if (!newUser.value.username.trim() || newUser.value.password.length < 6) {
    message.error('用户名必填，密码至少 6 位')
    return
  }
  try {
    await api.createUser({ ...newUser.value, username: newUser.value.username.trim() })
    message.success('已创建用户')
    showCreate.value = false
    await load()
  } catch (e: any) {
    message.error(e?.message || '创建失败')
  }
}

function openEdit(u: any) {
  editUser.value = u
  editForm.value = { display_name: u.display_name || '', role: u.role, status: u.status }
  showEdit.value = true
}
async function submitEdit() {
  if (!editUser.value) return
  try {
    await api.updateUser(editUser.value.id, { ...editForm.value })
    message.success('用户信息已更新')
    showEdit.value = false
    await load()
  } catch (e: any) {
    message.error(e?.message || '更新失败')
  }
}

function openReset(u: any) {
  resetId.value = u.id
  resetUsername.value = u.username
  resetPwd.value = ''
  showReset.value = true
}
async function submitReset() {
  if (resetPwd.value.length < 6) {
    message.error('密码至少 6 位')
    return
  }
  try {
    await api.resetUserPassword(resetId.value!, resetPwd.value)
    message.success('密码已重置')
    showReset.value = false
  } catch (e: any) {
    message.error(e?.message || '重置失败')
  }
}

function setStatus(u: any, status: string) {
  api.updateUser(u.id, { status })
    .then(() => { u.status = status; message.success(status === 'active' ? '已启用' : '已停用') })
    .catch((e: any) => message.error(e?.message || '操作失败'))
}
function changeRole(u: any, role: string) {
  api.updateUser(u.id, { role })
    .then(() => { u.role = role; u.role_label = roleLabel(role); message.success('角色已更新') })
    .catch((e: any) => message.error(e?.message || '操作失败'))
}
async function removeUser(u: any) {
  try {
    await api.deleteUser(u.id)
    message.success('已删除')
    await load()
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

function toggleSelectAll() {
  const pageIds = pagedUsers.value.map((u) => u.id)
  const allSelected = pageIds.every((id) => selectedIds.value.includes(id))
  if (allSelected) {
    selectedIds.value = selectedIds.value.filter((id) => !pageIds.includes(id))
  } else {
    selectedIds.value = Array.from(new Set([...selectedIds.value, ...pageIds]))
  }
}
function isAllSelected() {
  const pageIds = pagedUsers.value.map((u) => u.id)
  return pageIds.length > 0 && pageIds.every((id) => selectedIds.value.includes(id))
}

async function bulkAction(action: 'active' | 'disabled' | 'delete') {
  const ids = selectedIds.value.slice()
  if (ids.length === 0) {
    message.warning('请先选择用户')
    return
  }
  if (action === 'delete') {
    dialog.warning({
      title: '批量删除', content: `确认删除选中的 ${ids.length} 位用户？此操作不可恢复。`,
      positiveText: '删除', negativeText: '取消',
      onPositiveClick: async () => {
        await runBulk(ids, (id) => api.deleteUser(id), '已删除')
      },
    })
  } else {
    await runBulk(ids, (id) => api.updateUser(id, { status: action }), action === 'active' ? '已启用' : '已停用')
  }
}
async function runBulk(ids: number[], fn: (id: number) => Promise<any>, successText: string) {
  loading.value = true
  let ok = 0
  let fail = 0
  for (const id of ids) {
    try {
      await fn(id)
      ok++
    } catch {
      fail++
    }
  }
  loading.value = false
  if (fail === 0) message.success(`${successText} ${ok} 位用户`)
  else message.warning(`成功 ${ok} 位，失败 ${fail} 位`)
  selectedIds.value = []
  await load()
}

onMounted(load)
</script>

<template>
  <div class="users-view">
    <header class="page-header">
      <div>
        <div class="header-title">用户管理</div>
        <div class="header-sub">多用户账户与角色（RBAC）</div>
      </div>
      <NButton type="primary" size="small" :loading="loading" @click="openCreate">
        <template #icon><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></template>
        新建用户
      </NButton>
    </header>

    <div class="users-content">
      <!-- 统计卡片 -->
      <div class="stat-cards">
        <NCard size="small" class="stat-card">
          <div class="stat-value">{{ stats.total }}</div>
          <div class="stat-label">用户总数</div>
        </NCard>
        <NCard size="small" class="stat-card">
          <div class="stat-value" style="color: var(--success)">{{ stats.active }}</div>
          <div class="stat-label">已启用</div>
        </NCard>
        <NCard size="small" class="stat-card">
          <div class="stat-value" style="color: var(--warning)">{{ stats.disabled }}</div>
          <div class="stat-label">已停用</div>
        </NCard>
        <NCard size="small" class="stat-card">
          <div class="stat-value" style="color: var(--error)">{{ stats.admins }}</div>
          <div class="stat-label">管理员</div>
        </NCard>
      </div>

      <!-- 工具栏 -->
      <div class="toolbar">
        <NSpace align="center" :wrap="true">
          <NInputGroup>
            <NInput v-model:value="search" placeholder="搜索用户名 / 显示名 / ID" clearable style="width: 220px" />
            <NSelect v-model:value="filterRole" :options="roleOptions" placeholder="角色" style="width: 150px" />
            <NSelect v-model:value="filterStatus" :options="statusOptions" placeholder="状态" style="width: 120px" />
          </NInputGroup>
          <NButton size="small" :loading="loading" @click="load">刷新</NButton>
        </NSpace>
        <NSpace v-if="selectedIds.length > 0" align="center">
          <span class="selected-hint">已选 {{ selectedIds.length }} 位用户</span>
          <NButton size="tiny" @click="bulkAction('active')">批量启用</NButton>
          <NButton size="tiny" @click="bulkAction('disabled')">批量停用</NButton>
          <NButton size="tiny" type="error" @click="bulkAction('delete')">批量删除</NButton>
        </NSpace>
      </div>

      <!-- 表格 -->
      <div v-if="loading && users.length === 0" class="loading-wrap"><NSpin size="medium" /></div>
      <div v-else-if="filteredUsers.length === 0" class="empty-wrap">
        <NEmpty :description="users.length === 0 ? '暂无用户' : '没有匹配的用户'" />
      </div>
      <div v-else class="table-wrap">
        <table class="user-table">
          <thead>
            <tr>
              <th class="col-check"><NCheckbox :checked="isAllSelected()" @update:checked="toggleSelectAll" /></th>
              <th class="sortable" @click="toggleSort('id')">ID <SortIcon :asc="sortAsc" :active="sortKey === 'id'" /></th>
              <th class="sortable" @click="toggleSort('username')">用户名 <SortIcon :asc="sortAsc" :active="sortKey === 'username'" /></th>
              <th>显示名</th>
              <th>角色</th>
              <th>状态</th>
              <th class="sortable" @click="toggleSort('created_at')">创建时间 <SortIcon :asc="sortAsc" :active="sortKey === 'created_at'" /></th>
              <th class="sortable" @click="toggleSort('last_login')">最后登录 <SortIcon :asc="sortAsc" :active="sortKey === 'last_login'" /></th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in pagedUsers" :key="u.id">
              <td class="col-check"><NCheckbox :checked="selectedIds.includes(u.id)" @update:checked="(v: boolean) => { if (v) selectedIds.push(u.id); else selectedIds = selectedIds.filter((id) => id !== u.id) }" /></td>
              <td class="mono">{{ u.id }}</td>
              <td>
                <div class="user-cell">
                  <div class="avatar">{{ (u.display_name || u.username || '?').charAt(0).toUpperCase() }}</div>
                  <div>
                    <div class="username">{{ u.username }}</div>
                    <div v-if="u.display_name" class="display-name">{{ u.display_name }}</div>
                  </div>
                </div>
              </td>
              <td>{{ u.display_name || '—' }}</td>
              <td>
                <NTooltip :style="{ maxWidth: '320px' }">
                  <template #trigger>
                    <NTag size="small" :type="roleColor(u.role)" :bordered="false">{{ roleLabel(u.role) }}</NTag>
                  </template>
                  <div class="perm-list">
                    <div class="perm-title">{{ roleLabel(u.role) }} 权限</div>
                    <div v-for="p in rolePerms(u.role)" :key="p" class="perm-item">{{ p }}</div>
                  </div>
                </NTooltip>
              </td>
              <td>
                <NTag size="small" :type="u.status === 'active' ? 'success' : 'warning'" :bordered="false">
                  {{ u.status === 'active' ? '启用' : '停用' }}
                </NTag>
              </td>
              <td class="mono muted">{{ formatDate(u.created_at) }}</td>
              <td class="mono muted">{{ formatDate(u.last_login) }}</td>
              <td class="actions">
                <NButton size="tiny" quaternary @click="openEdit(u)">编辑</NButton>
                <NButton v-if="u.status !== 'active'" size="tiny" quaternary @click="setStatus(u, 'active')">启用</NButton>
                <NButton v-else size="tiny" quaternary @click="setStatus(u, 'disabled')">停用</NButton>
                <NButton size="tiny" quaternary @click="openReset(u)">重置密码</NButton>
                <NPopconfirm @positive-click="removeUser(u)">
                  <template #trigger><NButton size="tiny" quaternary type="error">删除</NButton></template>
                  确认删除用户「{{ u.username }}」？
                </NPopconfirm>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 分页 -->
      <div v-if="totalFiltered > pageSize" class="pagination-wrap">
        <NPagination v-model:page="page" :page-size="pageSize" :item-count="totalFiltered" show-size-picker :page-sizes="[10, 20, 50]" />
      </div>
    </div>

    <!-- 新建用户 -->
    <NModal v-model:show="showCreate" preset="card" title="新建用户" style="width: 460px; max-width: calc(100vw - 32px)">
      <NForm label-placement="top">
        <NFormItem label="用户名"><NInput v-model:value="newUser.username" placeholder="登录用户名" /></NFormItem>
        <NFormItem label="显示名"><NInput v-model:value="newUser.display_name" placeholder="可选" /></NFormItem>
        <NFormItem label="密码">
          <NInput v-model:value="newUser.password" type="password" placeholder="至少 6 位" />
          <div v-if="newUser.password" class="pwd-strength">
            <span :class="`pwd-strength-${createPwd.color}`">强度：{{ createPwd.text }}</span>
          </div>
        </NFormItem>
        <NFormItem label="角色"><NSelect v-model:value="newUser.role" :options="rolePlainOptions" /></NFormItem>
      </NForm>
      <template #footer><div class="modal-footer"><NButton @click="showCreate = false">取消</NButton><NButton type="primary" @click="submitCreate">创建</NButton></div></template>
    </NModal>

    <!-- 编辑用户 -->
    <NModal v-model:show="showEdit" preset="card" title="编辑用户" style="width: 460px; max-width: calc(100vw - 32px)">
      <NForm label-placement="top">
        <NFormItem label="用户名">
          <NInput :value="editUser?.username" disabled />
        </NFormItem>
        <NFormItem label="显示名"><NInput v-model:value="editForm.display_name" placeholder="可选" /></NFormItem>
        <NFormItem label="角色"><NSelect v-model:value="editForm.role" :options="rolePlainOptions" /></NFormItem>
        <NFormItem label="状态">
          <NSelect v-model:value="editForm.status" :options="statusOptions.filter((o) => o.value !== null)" />
        </NFormItem>
      </NForm>
      <template #footer><div class="modal-footer"><NButton @click="showEdit = false">取消</NButton><NButton type="primary" @click="submitEdit">保存</NButton></div></template>
    </NModal>

    <!-- 重置密码 -->
    <NModal v-model:show="showReset" preset="card" :title="`重置密码：${resetUsername}`" style="width: 420px; max-width: calc(100vw - 32px)">
      <NForm label-placement="top">
        <NFormItem label="新密码">
          <NInput v-model:value="resetPwd" type="password" placeholder="至少 6 位" />
          <div v-if="resetPwd" class="pwd-strength">
            <span :class="`pwd-strength-${resetPwdStrength.color}`">强度：{{ resetPwdStrength.text }}</span>
          </div>
        </NFormItem>
      </NForm>
      <template #footer><div class="modal-footer"><NButton @click="showReset = false">取消</NButton><NButton type="primary" @click="submitReset">确定</NButton></div></template>
    </NModal>
  </div>
</template>

<script lang="ts">
import { defineComponent, h } from 'vue'

const SortIcon = defineComponent({
  props: { asc: Boolean, active: Boolean },
  setup(props) {
    return () =>
      h(
        'span',
        {
          class: 'sort-icon',
          style: { opacity: props.active ? 1 : 0.3 },
        },
        props.active ? (props.asc ? '▲' : '▼') : '⇅',
      )
  },
})
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;
.users-view { height: calc(100 * var(--vh)); display: flex; flex-direction: column; }
.users-content { flex: 1; overflow-y: auto; padding: 20px; }

.stat-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
.stat-card {
  :deep(.n-card__content) { padding: 16px; }
  .stat-value { font-size: 28px; font-weight: 700; color: $text-primary; line-height: 1; margin-bottom: 6px; }
  .stat-label { font-size: 12px; color: $text-muted; }
}
@media (max-width: 900px) {
  .stat-cards { grid-template-columns: repeat(2, 1fr); }
}

.toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.selected-hint { font-size: 13px; color: $text-muted; }

.loading-wrap, .empty-wrap { padding: 48px 0; display: flex; justify-content: center; }
.table-wrap { overflow-x: auto; }

.user-table { width: 100%; border-collapse: collapse; font-size: 13px;
  th { text-align: left; font-weight: 600; color: $text-muted; font-size: 12px; padding: 10px 12px; border-bottom: 1px solid $border-color; white-space: nowrap; }
  td { padding: 10px 12px; border-bottom: 1px solid $border-light; color: $text-secondary; vertical-align: middle; }
  tbody tr:hover { background: rgba(var(--accent-primary-rgb), 0.03); }
  .col-check { width: 40px; padding-left: 4px; }
  .actions { display: flex; gap: 2px; flex-wrap: wrap; }
  .sortable { cursor: pointer; user-select: none; }
  .sort-icon { margin-left: 4px; font-size: 10px; }
}

.user-cell { display: flex; align-items: center; gap: 10px;
  .avatar { width: 32px; height: 32px; border-radius: 50%; background: $bg-secondary; color: $text-primary; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; flex-shrink: 0; }
  .username { color: $text-primary; font-weight: 500; }
  .display-name { font-size: 12px; color: $text-muted; margin-top: 2px; }
}

.mono { font-family: $font-code; }
.muted { color: $text-muted; }

.modal-footer { display: flex; justify-content: flex-end; gap: 8px; }

.pwd-strength { margin-top: 6px; font-size: 12px;
  .pwd-strength-error { color: var(--error); }
  .pwd-strength-warning { color: var(--warning); }
  .pwd-strength-success { color: var(--success); }
}

.perm-list {
  .perm-title { font-weight: 600; margin-bottom: 6px; color: $text-primary; }
  .perm-item { font-size: 12px; color: $text-secondary; line-height: 1.6; }
}

.pagination-wrap { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>
