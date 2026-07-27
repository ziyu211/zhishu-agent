import { ref } from 'vue'

// 主题管理：在 <html> 上切换 .dark，持久化到 localStorage。
const STORAGE_KEY = 'zhishu_theme'
const isDark = ref(localStorage.getItem(STORAGE_KEY) === 'dark')

function apply(dark: boolean) {
  const root = document.documentElement
  // 平滑过渡
  root.classList.add('theme-transitioning')
  if (dark) root.classList.add('dark')
  else root.classList.remove('dark')
  setTimeout(() => root.classList.remove('theme-transitioning'), 320)
  localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light')
}

// 初始化
if (isDark.value) document.documentElement.classList.add('dark')

export function useTheme() {
  function toggle() {
    isDark.value = !isDark.value
    apply(isDark.value)
  }
  function setDark(dark: boolean) {
    isDark.value = dark
    apply(dark)
  }
  return { isDark, toggle, setDark }
}
