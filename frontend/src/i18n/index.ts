/**
 * i18n 入口（vue-i18n，Composition API 模式）。
 * 目前仅注册中文；新增语言只需在 locales/ 下加文件并并入 messages。
 */
import { createI18n } from 'vue-i18n'
import zh from './locales/zh'

export const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'zh',
  messages: {
    zh,
  },
})

export default i18n
