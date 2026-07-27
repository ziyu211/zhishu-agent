import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import { injectTokens } from './styles/tokens'
import './styles/variables.scss'
import './styles/global.scss'

// 在挂载前注入设计令牌（单源：styles/tokens.ts），避免首屏闪烁
injectTokens()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
app.mount('#app')
