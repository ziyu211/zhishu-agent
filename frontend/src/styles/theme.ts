import type { GlobalThemeOverrides } from 'naive-ui'
import { tokens } from './tokens'

// 智枢智能体 · 主题覆盖（派生自 styles/tokens.ts 单源，避免配色双轨发散）
// 参照 hermes-web-ui「黑白水墨 Pure Ink」：纯黑白灰、深墨色(#2b2b2b)作强调色，
// 中国红仅用于品牌标识(Logo)，不进入功能色板。

const FONT_UI = '"PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", system-ui, -apple-system, sans-serif'
const FONT_MONO = '"JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace'

function common(isDark: boolean) {
  const t = isDark ? tokens.dark : tokens.light
  return {
    primaryColor: t['--accent-primary'],
    primaryColorHover: t['--accent-hover'],
    primaryColorPressed: isDark ? '#ffffff' : '#000000',
    primaryColorSuppl: t['--accent-primary'],
    bodyColor: t['--bg-primary'],
    cardColor: t['--bg-card'],
    modalColor: t['--bg-card'],
    popoverColor: t['--bg-card'],
    tableColor: t['--bg-card'],
    inputColor: t['--bg-input'],
    actionColor: t['--bg-secondary'],
    textColorBase: t['--text-primary'],
    textColor1: t['--text-primary'],
    textColor2: t['--text-secondary'],
    textColor3: t['--text-muted'],
    dividerColor: t['--border-color'],
    borderColor: t['--border-color'],
    hoverColor: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.04)',
    borderRadius: '8px',
    borderRadiusSmall: '6px',
    fontSize: '14px',
    fontSizeMedium: '14px',
    heightMedium: '36px',
    fontFamily: FONT_UI,
    fontFamilyMono: FONT_MONO,
  }
}

export const lightThemeOverrides: GlobalThemeOverrides = {
  common: common(false),
  Layout: { color: tokens.light['--bg-primary'], siderColor: tokens.light['--bg-sidebar'], headerColor: tokens.light['--bg-primary'] },
  Menu: {
    itemTextColorActive: tokens.light['--text-primary'],
    itemTextColorActiveHover: tokens.light['--text-primary'],
    itemIconColorActive: tokens.light['--text-primary'],
    itemColorActive: 'rgba(0, 0, 0, 0.06)',
    itemColorActiveHover: 'rgba(0, 0, 0, 0.1)',
    arrowColorActive: tokens.light['--text-primary'],
  },
  Button: {
    textColorPrimary: tokens.light['--text-on-accent'],
    colorPrimary: tokens.light['--accent-primary'],
    colorHoverPrimary: tokens.light['--accent-hover'],
    colorPressedPrimary: '#000000',
  },
  Input: {
    color: tokens.light['--bg-input'],
    colorFocus: tokens.light['--bg-input'],
    border: '1px solid ' + tokens.light['--border-color'],
    borderHover: '1px solid ' + tokens.light['--text-muted'],
    borderFocus: '1px solid ' + tokens.light['--accent-primary'],
    placeholderColor: tokens.light['--text-muted'],
    caretColor: tokens.light['--text-primary'],
  },
  Card: { color: tokens.light['--bg-card'], borderColor: tokens.light['--border-color'] },
  Modal: { color: tokens.light['--bg-card'] },
  Tag: { borderRadius: '6px' },
  Switch: {
    railColor: '#d0d0d0',
    railColorActive: tokens.light['--accent-primary'],
    loadingColor: tokens.light['--accent-primary'],
    opacityDisabled: 0.4,
  },
}

export const darkThemeOverrides: GlobalThemeOverrides = {
  common: common(true),
  Layout: { color: tokens.dark['--bg-primary'], siderColor: tokens.dark['--bg-sidebar'], headerColor: tokens.dark['--bg-primary'] },
  Menu: {
    itemTextColorActive: tokens.dark['--text-primary'],
    itemTextColorActiveHover: tokens.dark['--text-primary'],
    itemIconColorActive: tokens.dark['--text-primary'],
    itemColorActive: 'rgba(255, 255, 255, 0.08)',
    itemColorActiveHover: 'rgba(255, 255, 255, 0.12)',
    arrowColorActive: tokens.dark['--text-primary'],
  },
  Button: {
    textColorPrimary: tokens.dark['--text-on-accent'],
    colorPrimary: tokens.dark['--accent-primary'],
    colorHoverPrimary: tokens.dark['--accent-hover'],
    colorPressedPrimary: '#ffffff',
  },
  Input: {
    color: tokens.dark['--bg-input'],
    colorFocus: tokens.dark['--bg-input'],
    border: '1px solid ' + tokens.dark['--border-color'],
    borderHover: '1px solid ' + tokens.dark['--text-muted'],
    borderFocus: '1px solid ' + tokens.dark['--accent-primary'],
    placeholderColor: tokens.dark['--text-muted'],
    caretColor: tokens.dark['--text-primary'],
  },
  Card: { color: tokens.dark['--bg-card'], borderColor: tokens.dark['--border-color'] },
  Modal: { color: tokens.dark['--bg-card'] },
  Tag: { borderRadius: '6px' },
  Switch: {
    railColor: tokens.dark['--border-color'],
    railColorActive: tokens.dark['--success'],
    loadingColor: tokens.dark['--accent-primary'],
    opacityDisabled: 0.4,
  },
}

export function getThemeOverrides(isDark: boolean): GlobalThemeOverrides {
  return isDark ? darkThemeOverrides : lightThemeOverrides
}
