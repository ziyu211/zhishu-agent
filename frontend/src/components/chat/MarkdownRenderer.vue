<script setup lang="ts">
import { computed, ref, nextTick, onMounted, watch } from 'vue'
import hljs from 'highlight.js/lib/common'

const props = defineProps<{ content: string }>()

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * 链接 URL 白名单清洗：仅允许 http(s)/mailto，且禁止任何可破坏属性边界的字符。
 * 防止 `](https://x" onmouseover=alert(1))` 这类通过 Markdown 注入的 XSS。
 */
function sanitizeUrl(u: string): string {
  const t = (u || '').trim()
  if (!/^(https?:\/\/|mailto:)/i.test(t)) return '#'
  if (/["'<>()\s]/.test(t)) return '#'
  return t
}

function inline(s: string): string {
  let t = escapeHtml(s)
  // 行内代码
  t = t.replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`)
  // 粗体
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  // 斜体
  t = t.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
  // 链接（URL 经白名单清洗后写入属性）
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, (_m, a, u) => {
    const safe = sanitizeUrl(u)
    return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${a}</a>`
  })
  return t
}

function highlight(src: string, lang: string): string {
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(src, { language: lang }).value
    }
    return hljs.highlightAuto(src).value
  } catch {
    return escapeHtml(src)
  }
}

function render(src: string): string {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  let html = ''
  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // 代码围栏
    const fence = line.match(/^```(\w*)\s*$/)
    if (fence) {
      const lang = fence[1] || ''
      const buf: string[] = []
      i++
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i])
        i++
      }
      i++ // 跳过闭合 ```
      const code = buf.join('\n')
      html += `<div class="hljs-code-block"><div class="code-header"><span class="code-lang">${escapeHtml(lang || 'text')}</span><button class="code-copy" type="button">复制</button></div><pre><code class="hljs">${highlight(code, lang)}</code></pre></div>`
      continue
    }

    // 标题
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      const lvl = h[1].length
      html += `<h${lvl}>${inline(h[2])}</h${lvl}>`
      i++
      continue
    }

    // 分隔线
    if (/^(\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
      html += '<hr/>'
      i++
      continue
    }

    // 引用
    if (/^>\s?/.test(line)) {
      const buf: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ''))
        i++
      }
      html += `<blockquote>${inline(buf.join(' '))}</blockquote>`
      continue
    }

    // 表格（含表头分隔行）
    if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = line.split('|').slice(1, -1).map((c) => c.trim())
      i += 2
      const rows: string[] = []
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].split('|').slice(1, -1).map((c) => c.trim()).join('</td><td>'))
        i++
      }
      const thead = head.map((c) => `<th>${inline(c)}</th>`).join('')
      const tbody = rows.map((r) => `<tr><td>${r}</td></tr>`).join('')
      html += `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`
      continue
    }

    // 有序 / 无序列表
    if (/^(\s*)([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\d+\.\s+/.test(line)
      const buf: string[] = []
      while (i < lines.length && /^(\s*)([-*+]|\d+\.)\s+/.test(lines[i])) {
        buf.push(lines[i].replace(/^(\s*)([-*+]|\d+\.)\s+/, ''))
        i++
      }
      const items = buf.map((b) => `<li>${inline(b)}</li>`).join('')
      html += ordered ? `<ol>${items}</ol>` : `<ul>${items}</ul>`
      continue
    }

    // 空行
    if (line.trim() === '') {
      i++
      continue
    }

    // 段落（聚合连续非空行）
    const para: string[] = []
    while (i < lines.length && lines[i].trim() !== '' && !/^(#{1,6}\s|>\s?|```|\s*[-*+]\s|\s*\d+\.\s)/.test(lines[i]) && !/^(\*{3,}|-{3,}|_{3,})\s*$/.test(lines[i])) {
      para.push(lines[i])
      i++
    }
    html += `<p>${inline(para.join('<br/>'))}</p>`
  }
  return html
}

const body = ref<HTMLElement | null>(null)
const renderedHtml = computed(() => render(props.content || ''))

async function handleCopy(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest('.code-copy')
  if (!btn) return
  const block = btn.closest('.hljs-code-block')
  const code = block?.querySelector('code')?.textContent || ''
  try {
    await navigator.clipboard.writeText(code)
    btn.textContent = '已复制'
    setTimeout(() => (btn.textContent = '复制'), 1500)
  } catch {
    btn.textContent = '复制失败'
  }
}

onMounted(() => void nextTick())
watch(renderedHtml, () => void nextTick())
</script>

<template>
  <div ref="body" class="markdown-body" v-html="renderedHtml" @click="handleCopy"></div>
</template>

<style lang="scss">
@use '@/styles/variables' as *;

.markdown-body {
  font-size: 14px;
  line-height: 1.65;
  min-width: 0;
  max-width: 100%;
  word-break: break-word;

  p { margin: 0 0 8px; &:last-child { margin-bottom: 0; } }
  ul, ol { padding-left: 20px; margin: 4px 0 8px; }
  li { margin: 2px 0; }
  strong { color: $text-primary; font-weight: 600; }
  em { color: $text-secondary; }
  a { color: $accent-primary; text-decoration: underline; text-underline-offset: 2px;
    &:hover { color: $accent-hover; } }
  blockquote { margin: 8px 0; padding: 4px 12px; border-left: 3px solid $border-color; color: $text-secondary; }
  code:not(.hljs) { background: $code-bg; padding: 2px 6px; border-radius: 4px; font-family: $font-code; font-size: 13px; color: $accent-primary; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0; display: block; overflow-x: auto;
    th, td { padding: 6px 12px; border: 1px solid $border-color; text-align: left; font-size: 13px; }
    th { background: rgba(var(--accent-primary-rgb), 0.08); color: $text-primary; font-weight: 600; }
    td { color: $text-secondary; } }
  hr { border: none; border-top: 1px solid $border-color; margin: 12px 0; }

  .hljs-code-block { margin: 10px 0; border: 1px solid $border-color; border-radius: 8px; overflow: hidden; background: $code-bg;
    .code-header { display: flex; align-items: center; justify-content: space-between; padding: 4px 10px; background: rgba(var(--accent-primary-rgb), 0.04); border-bottom: 1px solid $border-color; }
    .code-lang { font-size: 11px; color: $text-muted; font-family: $font-code; }
    .code-copy { border: none; background: none; color: $text-secondary; font-size: 11px; cursor: pointer; padding: 2px 6px; border-radius: 4px;
      &:hover { color: $text-primary; background: rgba(var(--accent-primary-rgb), 0.08); } }
    pre { margin: 0; padding: 10px 12px; overflow-x: auto; }
    code.hljs { font-family: $font-code; font-size: 13px; line-height: 1.55; background: transparent; padding: 0; }
  }
}
</style>
