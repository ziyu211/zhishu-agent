<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { api, setToken } from '@/api/client'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const route = useRoute()
const app = useAppStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

onMounted(() => {
  // 平滑切入
  document.documentElement.classList.remove('theme-transitioning')
})

async function handleLogin() {
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await api.login(username.value.trim(), password.value)
    setToken(res.token)
    app.setUser({
      user: res.user,
      role: res.role,
      role_label: res.role_label,
      display_name: res.display_name,
      perms: res.perms || [],
    })
    const redirect = (route.query.redirect as string) || '/chat'
    router.replace(redirect)
  } catch (err: any) {
    errorMsg.value = err?.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-view">
    <div class="login-card">
      <div class="login-logo">
        <span class="logo-mark">智</span>
      </div>
      <h1 class="login-title">智枢智能体</h1>
      <p class="login-desc">国产化智能体平台 · 本地部署 · 数据不出域</p>

      <form class="login-form" @submit.prevent="handleLogin">
        <input v-model="username" type="text" class="login-input" placeholder="用户名" autofocus />
        <input v-model="password" type="password" class="login-input" placeholder="密码" @keyup.enter="handleLogin" />
        <div v-if="errorMsg" class="login-error">{{ errorMsg }}</div>
        <button type="submit" class="login-btn" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>
    </div>
    <div class="login-footer">Zhishu Agent · 纯国产技术栈</div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.login-view {
  height: calc(100 * var(--vh));
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: $bg-primary;
}

.login-card {
  width: 420px;
  max-width: calc(100vw - 32px);
  padding: 48px 44px;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  background: $bg-card;
  text-align: center;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);

  @media (max-width: $breakpoint-mobile) { padding: 32px 24px; }
}

.login-logo { margin-bottom: 20px; }
.logo-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 14px;
  background: var(--brand);
  color: #fff;
  font-size: 30px;
  font-weight: 700;
  font-family: $font-ui;
}

.login-title { font-size: 24px; font-weight: 600; color: $text-primary; margin: 0 0 8px; }
.login-desc { font-size: 13px; color: $text-muted; margin: 0 0 28px; line-height: 1.6; }

.login-form { display: flex; flex-direction: column; gap: 12px; }

.login-input {
  width: 100%;
  padding: 13px 16px;
  border: 1px solid $border-color;
  border-radius: $radius-sm;
  font-size: 15px;
  color: $text-primary;
  background: $bg-input;
  outline: none;
  transition: border-color $transition-fast;
  box-sizing: border-box;

  &::placeholder { color: $text-muted; }
  &:focus { border-color: $accent-primary; }
}

.login-error { font-size: 13px; color: $error; text-align: left; }

.login-btn {
  width: 100%;
  padding: 13px;
  border: none;
  border-radius: $radius-sm;
  background: $accent-primary;
  color: var(--text-on-accent);
  font-size: 15px;
  font-weight: 500;
  letter-spacing: 2px;
  cursor: pointer;
  transition: opacity $transition-fast;

  &:hover { background: $accent-hover; }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.login-footer { margin-top: 20px; font-size: 11px; color: $text-muted; letter-spacing: 0.5px; }
</style>
