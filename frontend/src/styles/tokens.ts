/**
 * 智枢智能体 —— 设计令牌（单源）
 * 对标 hermes-web-ui 的「黑白水墨 Pure Ink」：纯黑白灰、无彩色主调；
 * 中国红(--brand)仅用于 Logo 品牌标识，不进入功能色板。
 *
 * 这是**唯一**的配色来源：
 *  - CSS 变量（供组件 SCSS 的 var(--x) 引用）由 buildTokenCss()/injectTokens() 在运行时注入 <style>；
 *  - Naive UI 的 themeOverrides 由 getThemeOverrides() 从同一份 tokens 派生。
 * 修改配色只需改这里，避免 variables.scss 与 theme.ts 双轨发散。
 */

export type TokenMap = Record<string, string>

export const tokens: { light: TokenMap; dark: TokenMap } = {
  light: {
    '--bg-primary': '#fafafa',
    '--bg-secondary': '#f0f0f0',
    '--bg-sidebar': '#f5f5f5',
    '--bg-card': '#ffffff',
    '--bg-card-hover': '#fafafa',
    '--bg-input': '#ffffff',

    '--border-color': '#e0e0e0',
    '--border-light': '#ebebeb',

    '--accent-primary': '#2b2b2b',
    '--accent-hover': '#111111',
    '--accent-muted': '#888888',

    '--text-primary': '#1a1a1a',
    '--text-secondary': '#666666',
    '--text-muted': '#999999',

    '--success': '#2e7d32',
    '--error': '#c62828',
    '--warning': '#f57f17',

    '--msg-user-bg': '#f0f0f0',
    '--msg-assistant-bg': '#ffffff',
    '--msg-system-border': '#bdbdbd',

    '--code-bg': '#f4f4f4',

    '--brand': '#c8152d',

    '--text-on-accent': '#ffffff',

    '--accent-primary-rgb': '43, 43, 43',
    '--accent-hover-rgb': '17, 17, 17',
    '--text-primary-rgb': '26, 26, 26',
    '--text-muted-rgb': '153, 153, 153',
    '--success-rgb': '46, 125, 50',
    '--error-rgb': '198, 40, 40',
    '--warning-rgb': '245, 127, 23',
    '--brand-rgb': '200, 21, 45',
  },
  dark: {
    '--bg-primary': '#1a1a1a',
    '--bg-secondary': '#252525',
    '--bg-sidebar': '#202020',
    '--bg-card': '#2a2a2a',
    '--bg-card-hover': '#303030',
    '--bg-input': '#2a2a2a',

    '--border-color': '#3a3a3a',
    '--border-light': '#333333',

    '--accent-primary': '#e0e0e0',
    '--accent-hover': '#f5f5f5',
    '--accent-muted': '#888888',

    '--text-primary': '#f0f0f0',
    '--text-secondary': '#c0c0c0',
    '--text-muted': '#888888',

    '--success': '#66bb6a',
    '--error': '#ef5350',
    '--warning': '#ffb74d',

    '--msg-user-bg': '#2a2a2a',
    '--msg-assistant-bg': '#252525',
    '--msg-system-border': '#555555',

    '--code-bg': '#1e1e1e',

    '--brand': '#e0394c',

    '--text-on-accent': '#1a1a1a',

    '--accent-primary-rgb': '240, 240, 240',
    '--accent-hover-rgb': '245, 245, 245',
    '--text-primary-rgb': '240, 240, 240',
    '--text-muted-rgb': '136, 136, 136',
    '--success-rgb': '102, 187, 106',
    '--error-rgb': '239, 83, 80',
    '--warning-rgb': '255, 183, 77',
    '--brand-rgb': '224, 57, 76',
  },
}

/** 生成一段 :root / .dark 的 CSS 文本（含全部 CSS 变量）。 */
export function buildTokenCss(): string {
  const toCss = (map: TokenMap) =>
    Object.entries(map)
      .map(([k, v]) => `  ${k}: ${v};`)
      .join('\n')
  return `:root {\n${toCss(tokens.light)}\n}\n\n.dark {\n${toCss(tokens.dark)}\n}`
}

/** 将令牌注入 <head> 的 <style id="zhishu-tokens">（幂等，main.ts 在挂载前调用一次）。 */
export function injectTokens(): void {
  if (typeof document === 'undefined') return
  const id = 'zhishu-tokens'
  let el = document.getElementById(id) as HTMLStyleElement | null
  if (!el) {
    el = document.createElement('style')
    el.id = id
    document.head.appendChild(el)
  }
  el.textContent = buildTokenCss()
}
