<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NModal, NInput, NForm, NFormItem, NSelect, useMessage } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'
import { api } from '@/api/client'
import { request } from '@/api/http'
import { actAs, setActAs, clearActAs } from '@/api/actas'

const app = useAppStore()
const { t } = useI18n()
const router = useRouter()
const message = useMessage()

const user = computed(() => app.user)
const display = computed(() => user.value?.display_name || user.value?.user || '?')
const initial = computed(() => (display.value[0] || '?').toUpperCase())

function doLogout() {
  app.logout()
  router.replace('/login')
}

// ─── 管理员「切换用户（代管）」 ───────────────────────
const userOptions = ref<{ label: string; value: string }[]>([])
const isAdmin = computed(() => app.isAdmin)
async function loadUsers() {
  if (!isAdmin.value) return
  try {
    const res = await request<{ users: any[] }>('/users')
    userOptions.value = (res.users || [])
      .filter((u) => u.username !== user.value?.user)
      .map((u) => ({ label: `${u.display_name || u.username}（${u.role}）`, value: u.username }))
  } catch {}
}
onMounted(loadUsers)

function onActAsChange(v: string | null) {
  if (!v) {
    clearActAs()
  } else {
    setActAs(v)
    message.info(`已切换为以「${v}」身份查看 / 配置`)
  }
}

// ─── 自助修改密码 ──────────────────────────────────────
const showPwd = ref(false)
const pwdForm = ref({ old_password: '', new_password: '' })
const pwdLoading = ref(false)

async function submitPwd() {
  if (!pwdForm.value.old_password || pwdForm.value.new_password.length < 6) {
    message.warning('请输入原密码，且新密码至少 6 位')
    return
  }
  pwdLoading.value = true
  try {
    await api.changePassword({ ...pwdForm.value })
    message.success('密码已修改，请使用新密码重新登录')
    showPwd.value = false
    pwdForm.value = { old_password: '', new_password: '' }
    app.logout()
    router.replace('/login')
  } catch (e: any) {
    message.error(e?.message || '修改失败')
  } finally {
    pwdLoading.value = false
  }
}
</script>

<template>
  <div class="user-menu">
    <div class="user-info">
      <div class="avatar">{{ initial }}</div>
      <div class="meta">
        <div class="name">{{ display }}</div>
        <div class="role">{{ user?.role_label || user?.role }}</div>
      </div>
    </div>

    <!-- 管理员：切换用户（代管） -->
    <div v-if="isAdmin" class="act-as">
      <div class="act-as-label">
        代管用户
        <span v-if="actAs" class="act-as-tag">当前：{{ actAs }}</span>
      </div>
      <NSelect
        :value="actAs || null"
        :options="userOptions"
        placeholder="以自己的身份操作"
        clearable
        size="small"
        @update:value="onActAsChange"
      />
    </div>

    <NButton block size="small" quaternary class="menu-item" @click="showPwd = true">
      修改密码
    </NButton>
    <NButton block size="small" quaternary class="logout-item" @click="doLogout">
      {{ t('common.logout') }}
    </NButton>

    <NModal v-model:show="showPwd" preset="card" title="修改密码" style="width: 420px; max-width: calc(100vw - 32px)">
      <NForm label-placement="top">
        <NFormItem label="原密码">
          <NInput v-model:value="pwdForm.old_password" type="password" placeholder="请输入当前密码" />
        </NFormItem>
        <NFormItem label="新密码">
          <NInput v-model:value="pwdForm.new_password" type="password" placeholder="至少 6 位" />
        </NFormItem>
      </NForm>
      <template #footer>
        <div class="modal-footer">
          <NButton @click="showPwd = false">取消</NButton>
          <NButton type="primary" :loading="pwdLoading" @click="submitPwd">确定</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.user-menu {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.act-as {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 8px;
  border-top: 1px solid rgba(var(--border-color-rgb), 0.6);
  border-bottom: 1px solid rgba(var(--border-color-rgb), 0.6);

  .act-as-label {
    font-size: 11px;
    color: $text-muted;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .act-as-tag {
    color: $accent-primary;
    font-weight: 600;
  }
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;

  .avatar {
    width: 30px;
    height: 30px;
    border-radius: 50%;
    background: rgba(var(--accent-primary-rgb), 0.12);
    color: $accent-primary;
    font-weight: 600;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .meta {
    min-width: 0;
    .name {
      font-size: 13px;
      font-weight: 600;
      color: $text-primary;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .role {
      font-size: 11px;
      color: $text-muted;
    }
  }
}

.logout-item {
  color: $text-muted;
  &:hover {
    color: $error !important;
  }
}

.menu-item {
  color: $text-secondary;
  justify-content: flex-start;
  &:hover {
    color: $accent-primary !important;
  }
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
