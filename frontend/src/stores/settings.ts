/**
 * 设置 store（客户端偏好，本地持久化；服务端配置经由各域 store 处理）。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

const KEY = 'zhishu_settings'

interface Prefs {
  density: 'comfortable' | 'compact'
  streamThinking: boolean
}

function loadPrefs(): Prefs {
  try {
    const v = localStorage.getItem(KEY)
    if (v) return { density: 'comfortable', streamThinking: true, ...JSON.parse(v) }
  } catch {
    /* noop */
  }
  return { density: 'comfortable', streamThinking: true }
}

export const useSettingsStore = defineStore('settings', () => {
  const prefs = ref<Prefs>(loadPrefs())

  function persist() {
    localStorage.setItem(KEY, JSON.stringify(prefs.value))
  }
  function setDensity(d: Prefs['density']) {
    prefs.value.density = d
    persist()
  }
  function setStreamThinking(v: boolean) {
    prefs.value.streamThinking = v
    persist()
  }

  return { prefs, setDensity, setStreamThinking }
})
