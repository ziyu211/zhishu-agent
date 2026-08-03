<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Msg } from '@/stores/chat'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { useDocViewer } from '@/composables/useDocViewer'
import { useChatStore } from '@/stores/chat'

const props = defineProps<{ message: Msg }>()
const toolExpanded = ref(false)
const { openDoc } = useDocViewer()
const chat = useChatStore()

const attachment = computed(() => props.message.attachment)
const ownerSessionId = computed(() => {
  const found = chat.sessions.find((s) => s.messages.some((x) => x.id === props.message.id))
  return found?.id || chat.activeId
})

// 全屏图片预览（借鉴 hermes-web-ui image-preview-overlay）
const previewUrl = ref<string | null>(null)
function openPreview(url?: string | null) {
  if (url) previewUrl.value = url
}

function openKb(docId?: string | null) {
  if (docId) openDoc(docId, attachment.value?.title || '文档')
}

/** 主动解析 / 失败重试：走 POST /api/v1/chat/parse，结果回填卡片并入知识库。
 *  状态由 store 切换为 parsing → done/error，模板自动响应，无需本地 loading 态。 */
async function reparse() {
  await chat.reparseAttachment(ownerSessionId.value, props.message.id)
}

function fmtDocSize(n?: number): string {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
const hasDocs = computed(() => !!props.message.docs && props.message.docs.length > 0)

const isSystem = computed(() => props.message.role === 'system')
const isUser = computed(() => props.message.role === 'user')
const isTool = computed(() => props.message.role === 'tool')

// 思考内容（部分模型以 <think>...</think> 包裹）
const parsed = computed(() => {
  const c = props.message.content || ''
  const m = c.match(/<think>([\s\S]*?)<\/think>/)
  if (m) {
    return { thinking: m[1].trim(), body: c.replace(/<think>[\s\S]*?<\/think>/, '').trim() }
  }
  return { thinking: '', body: c }
})
const hasThinking = computed(() => !!parsed.value.thinking)

const timeStr = computed(() => {
  const d = new Date(props.message.ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
})
</script>

<template>
  <div class="message" :class="message.role">
    <template v-if="isTool">
      <div class="tool-line" :class="{ expandable: message.toolArgs || message.toolResult }" @click="toolExpanded = !toolExpanded">
        <svg v-if="message.toolArgs || message.toolResult" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tool-chevron" :class="{ rotated: toolExpanded }"><polyline points="9 18 15 12 9 6" /></svg>
        <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="tool-icon"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></svg>
        <span class="tool-name">{{ message.toolName }}</span>
        <span v-if="message.toolStatus === 'running'" class="tool-spinner"></span>
        <span v-else-if="message.toolStatus === 'error'" class="tool-error-badge">错误</span>
      </div>
      <div v-if="toolExpanded && (message.toolArgs || message.toolResult)" class="tool-details">
        <div v-if="message.toolArgs" class="tool-detail-section">
          <div class="tool-detail-label">参数</div>
          <pre class="tool-detail-code">{{ message.toolArgs }}</pre>
        </div>
        <div v-if="message.toolResult" class="tool-detail-section">
          <div class="tool-detail-label">结果</div>
          <pre class="tool-detail-code">{{ message.toolResult }}</pre>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="msg-body">
        <div v-if="!isUser" class="msg-avatar">智</div>
        <div class="msg-content" :class="message.role">
          <div v-if="message.agent" class="agent-badge">▸ {{ message.agent }}</div>
          <div class="message-bubble" :class="{ system: isSystem }">
            <!-- 上传到对话框的附件（文档/图片就地解析；需插件时询问安装） -->
            <div v-if="attachment" class="attach-card" :class="{ image: attachment.is_image }">
              <div class="attach-card-head">
                <span class="attach-card-icon">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                </span>
                <span class="attach-card-name">{{ attachment.title }}</span>
                <span class="attach-card-type">{{ attachment.file_type }}</span>
              </div>

              <div v-if="attachment.is_image && attachment.url" class="attach-card-img" @click="openPreview(attachment.url)">
                <img :src="attachment.url" alt="上传图片" loading="lazy" />
                <span class="attach-img-hint">点击全屏预览</span>
              </div>

              <div v-if="attachment.status === 'parsing'" class="attach-status">
                <span class="gen-spinner"></span><span>解析中…</span>
              </div>
              <div v-else-if="attachment.status === 'installing'" class="attach-status">
                <span class="gen-spinner"></span><span>正在安装插件…</span>
              </div>

              <!-- 已解析：展示正文 + 知识库入口 -->
              <div v-else-if="attachment.parsed && attachment.text" class="attach-parsed">
                <details>
                  <summary>
                    已解析内容（共 {{ attachment.text_total ?? attachment.text.length }} 字，预览前 {{ attachment.text.length }} 字）
                  </summary>
                  <pre class="attach-parsed-text">{{ attachment.text }}</pre>
                </details>
                <div class="attach-actions">
                  <button v-if="attachment.doc_id" class="attach-kb-btn" type="button" @click="openKb(attachment.doc_id)">
                    查看知识库
                  </button>
                  <a v-if="attachment.url" class="attach-kb-btn" :href="attachment.url" :download="attachment.title" target="_blank" rel="noopener">下载原文件</a>
                </div>
              </div>

              <!-- 仅落盘：等待用户提问后 agent 自主读取 -->
              <div v-else-if="attachment.status === 'stored'" class="attach-need">
                <div class="attach-need-text">
                  <template v-if="attachment.is_image && attachment.vision_available">
                    已上传，可作为视觉参考发送对话。
                  </template>
                  <template v-else>
                    已上传，等待智能体读取…
                  </template>
                </div>
                <div class="attach-actions">
                  <button
                    v-if="!attachment.is_image"
                    class="attach-install-btn"
                    type="button"
                    @click="reparse"
                  >
                    立即解析正文
                  </button>
                  <a v-if="attachment.url" class="attach-kb-btn" :href="attachment.url" :download="attachment.title" target="_blank" rel="noopener">下载原文件</a>
                </div>
              </div>

              <!-- 已处理完成（内容已在上方解析块或对话回答中） -->
              <div v-else-if="attachment.status === 'done'" class="attach-need">
                <div class="attach-need-text">已解析处理完成。</div>
                <a v-if="attachment.url" class="attach-kb-btn" :href="attachment.url" :download="attachment.title" target="_blank" rel="noopener">下载原文件</a>
              </div>

              <!-- 已解析但无可提取文本（图片型/扫描件等） -->
              <div v-else-if="attachment.parsed && !attachment.text" class="attach-need">
                <div class="attach-need-text">已解析，但未提取到可检索文本内容（可能是纯图片或扫描件）。</div>
                <a v-if="attachment.url" class="attach-kb-btn" :href="attachment.url" :download="attachment.title" target="_blank" rel="noopener">下载原文件</a>
              </div>

              <!-- 解析错误：可重试（图片无 OCR 能力，重试无意义故不提供） -->
              <div v-else-if="attachment.parse_error" class="attach-need">
                <div class="gen-error">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" /></svg>
                  <span>{{ attachment.parse_error }}</span>
                </div>
                <div class="attach-actions">
                  <button
                    v-if="!attachment.is_image && attachment.stored_path"
                    class="attach-install-btn"
                    type="button"
                    @click="reparse"
                  >
                    重新解析
                  </button>
                  <a v-if="attachment.url" class="attach-kb-btn" :href="attachment.url" :download="attachment.title" target="_blank" rel="noopener">下载原文件</a>
                </div>
              </div>
            </div>

            <!-- 用户上传的参考图（图生图/图生视频） -->
            <a
              v-if="message.image"
              :href="message.image"
              target="_blank"
              rel="noopener"
              class="uploaded-image"
            >
              <img :src="message.image" alt="参考图" loading="lazy" />
            </a>

            <!-- 用户上传并关联的文档（点击打开查看器） -->
            <div v-if="hasDocs" class="msg-docs">
              <button
                v-for="d in message.docs"
                :key="d.doc_id"
                class="doc-card"
                type="button"
                @click="openDoc(d.doc_id, d.title)"
              >
                <span class="doc-card-icon">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                </span>
                <span class="doc-card-info">
                  <span class="doc-card-name">{{ d.title }}</span>
                  <span class="doc-card-sub">{{ d.file_type }}<template v-if="d.size"> · {{ fmtDocSize(d.size) }}</template></span>
                </span>
                <span class="doc-card-open">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                </span>
              </button>
            </div>

            <div v-if="hasThinking" class="thinking-block">
              <div class="thinking-header">💭 思考过程</div>
              <div class="thinking-body"><MarkdownRenderer :content="parsed.thinking" /></div>
            </div>
            <MarkdownRenderer v-if="parsed.body" :content="parsed.body" />

            <!-- 生成的图像 -->
            <div v-if="message.images && message.images.length" class="media-grid">
              <a
                v-for="(img, i) in message.images"
                :key="'img' + i"
                :href="img"
                target="_blank"
                rel="noopener"
                class="media-image"
              >
                <img :src="img" :alt="'生成图像 ' + (i + 1)" loading="lazy" />
              </a>
            </div>

            <!-- 生成的视频 -->
            <div v-if="message.videos && message.videos.length" class="media-videos">
              <video
                v-for="(vid, i) in message.videos"
                :key="'vid' + i"
                :src="vid"
                controls
                playsinline
                preload="metadata"
                class="media-video"
              ></video>
            </div>

            <!-- 生成状态（图像/视频进行中） -->
            <div v-if="message.statusText" class="gen-status">
              <span class="gen-spinner"></span>
              <span>{{ message.statusText }}</span>
            </div>

            <!-- 生成错误 -->
            <div v-if="message.errorText" class="gen-error">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" /></svg>
              <span>{{ message.errorText }}</span>
            </div>

            <span
              v-if="message.isStreaming && !message.content && !message.statusText && !(message.images && message.images.length) && !(message.videos && message.videos.length) && !message.errorText"
              class="streaming-dots"
            ><span></span><span></span><span></span></span>
          </div>
          <div class="message-time">{{ timeStr }}</div>
        </div>
      </div>
    </template>
  </div>

  <!-- 全屏图片预览 overlay（Teleport 到 body，借鉴 hermes-web-ui） -->
  <Teleport to="body">
    <div v-if="previewUrl" class="image-preview-overlay" @click.self="previewUrl = null">
      <button class="image-preview-close" type="button" @click="previewUrl = null" aria-label="关闭">×</button>
      <img class="image-preview-img" :src="previewUrl" alt="预览" @click="previewUrl = null" />
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.message {
  display: flex;
  flex-direction: column;

  &.user { align-items: flex-end; .msg-body { max-width: 78%; } .message-bubble { background-color: $msg-user-bg; border-radius: 10px; } }
  &.assistant { flex-direction: row; align-items: flex-start; gap: 8px; .msg-body { max-width: 82%; }
    .msg-avatar { width: 34px; height: 34px; flex-shrink: 0; border-radius: 8px; background: var(--brand); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 15px; margin-top: 2px; }
    .message-bubble { background-color: $msg-assistant-bg; border-radius: 10px; } }
  &.system { align-items: flex-start; .message-bubble.system { border-left: 3px solid $warning; border-radius: $radius-sm; max-width: 82%; background-color: rgba(var(--warning-rgb), 0.06); } }
}

.msg-body { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
.msg-content { display: flex; flex-direction: column; min-width: 0; }

.message-bubble {
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.65;
  word-break: break-word;
  border: 1px solid $border-color;
  border-radius: 10px;
}

.thinking-block { margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px dashed $border-light;
  .thinking-header { font-size: 11px; color: $text-muted; margin-bottom: 4px; }
  .thinking-body { font-size: 13px; opacity: 0.85; font-style: italic; border-left: 2px solid $border-light; padding-left: 10px; } }

.message-time { font-size: 11px; color: $text-muted; margin-top: 4px; padding: 0 4px; }

.agent-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  color: $accent-primary;
  background: rgba(var(--accent-primary-rgb), 0.1);
  border: 1px solid rgba(var(--accent-primary-rgb), 0.25);
  border-radius: 999px;
  padding: 1px 10px;
  margin-bottom: 4px;
  align-self: flex-start;
}

.tool-line { display: flex; align-items: center; gap: 6px; font-size: 11px; color: $text-muted; padding: 2px 4px; border-radius: $radius-sm;
  &.expandable { cursor: pointer; &:hover { background: rgba(0, 0, 0, 0.03); } }
  .tool-name { font-family: $font-code; flex-shrink: 0; } }

.tool-chevron { flex-shrink: 0; transition: transform 0.15s ease; &.rotated { transform: rotate(90deg); } }

.tool-spinner { width: 10px; height: 10px; border: 1.5px solid $text-muted; border-top-color: transparent; border-radius: 50%; animation: spin 0.6s linear infinite; flex-shrink: 0; }
.tool-error-badge { font-size: 9px; color: $error; background: rgba(var(--error-rgb), 0.08); padding: 0 4px; border-radius: 3px; line-height: 14px; }

.tool-details { margin-left: 16px; margin-top: 2px; border-left: 2px solid $border-light; padding-left: 10px; }
.tool-detail-section { margin-bottom: 6px; }
.tool-detail-label { font-size: 10px; font-weight: 600; color: $text-muted; text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 2px; }
.tool-detail-code { font-family: $font-code; font-size: 11px; white-space: pre-wrap; word-break: break-word; color: $text-secondary; background: $code-bg; border: 1px solid $border-light; border-radius: 4px; padding: 6px 8px; margin: 0; max-height: 240px; overflow: auto; }

.uploaded-image { display: block; margin-bottom: 8px; border: 1px solid $border-color; border-radius: 8px; overflow: hidden; line-height: 0; max-width: 220px;
  img { width: 100%; height: auto; display: block; } }

.msg-docs { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.doc-card {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 320px;
  padding: 8px 10px;
  background-color: $bg-card;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: $text-primary;
  transition: border-color $transition-fast, background-color $transition-fast;
  &:hover { border-color: rgba(var(--accent-primary-rgb), 0.5); background-color: rgba(var(--accent-primary-rgb), 0.06); }
  .dark & { background-color: #2a2a2a; }
}
.doc-card-icon {
  width: 34px; height: 34px; flex-shrink: 0;
  border-radius: $radius-sm;
  border: 1px solid $border-color;
  display: flex; align-items: center; justify-content: center;
  color: $accent-primary; background: rgba(var(--accent-primary-rgb), 0.08);
}
.doc-card-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.doc-card-name { font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.doc-card-sub { font-size: 11px; color: $text-muted; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.doc-card-open { flex-shrink: 0; color: $accent-primary; display: flex; opacity: 0; transition: opacity $transition-fast; }
.doc-card:hover .doc-card-open { opacity: 1; }

.media-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px;
  .media-image { display: block; border: 1px solid $border-color; border-radius: 8px; overflow: hidden; line-height: 0; max-width: 320px;
    img { width: 100%; height: auto; display: block; background: $code-bg; } } }

.media-videos { display: flex; flex-direction: column; gap: 8px; margin-top: 8px;
  .media-video { width: 100%; max-width: 420px; border: 1px solid $border-color; border-radius: 8px; background: #000; } }

.gen-status { display: inline-flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; color: $text-muted;
  .gen-spinner { width: 12px; height: 12px; border: 1.5px solid $text-muted; border-top-color: transparent; border-radius: 50%; animation: spin 0.7s linear infinite; flex-shrink: 0; } }

.gen-error { display: flex; align-items: center; gap: 6px; margin-top: 8px; font-size: 13px; color: $error; background: rgba(var(--error-rgb), 0.06); border: 1px solid rgba(var(--error-rgb), 0.2); border-radius: 6px; padding: 8px 10px;
  svg { flex-shrink: 0; } }

.streaming-dots { display: inline-flex; gap: 4px; padding: 4px 0;
  span { width: 6px; height: 6px; background-color: $text-muted; border-radius: 50%; animation: pulse 1.4s infinite ease-in-out;
    &:nth-child(2) { animation-delay: 0.2s; }
    &:nth-child(3) { animation-delay: 0.4s; } } }

.attach-card { display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; padding: 10px 12px;
  background-color: $bg-card; border: 1px solid $border-color; border-radius: $radius-md;
  .dark & { background-color: #2a2a2a; } }
.attach-card-head { display: flex; align-items: center; gap: 8px; }
.attach-card-icon { width: 30px; height: 30px; flex-shrink: 0; border-radius: $radius-sm; border: 1px solid $border-color;
  display: flex; align-items: center; justify-content: center; color: $accent-primary; background: rgba(var(--accent-primary-rgb), 0.08); }
.attach-card-name { flex: 1; min-width: 0; font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.attach-card-type { flex-shrink: 0; font-size: 10px; color: $text-muted; border: 1px solid $border-light; border-radius: 4px; padding: 1px 6px; text-transform: uppercase; }
.attach-card-img { position: relative; max-width: 260px; border: 1px solid $border-color; border-radius: $radius-sm; overflow: hidden; line-height: 0; cursor: zoom-in;
  img { width: 100%; height: auto; display: block; }
  .attach-img-hint { position: absolute; right: 6px; bottom: 6px; font-size: 10px; line-height: 1.4; padding: 2px 6px; border-radius: 4px;
    background: rgba(0, 0, 0, 0.55); color: #fff; opacity: 0; transition: opacity $transition-fast; pointer-events: none; }
  &:hover .attach-img-hint { opacity: 1; } }

.attach-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }

.attach-status { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: $text-muted;
  .gen-spinner { width: 12px; height: 12px; border: 1.5px solid $text-muted; border-top-color: transparent; border-radius: 50%; animation: spin 0.7s linear infinite; flex-shrink: 0; } }

.attach-need { display: flex; flex-direction: column; gap: 8px; }
.attach-need-text { font-size: 12px; color: $text-secondary; line-height: 1.5; }
.attach-install-btn, .attach-kb-btn {
  align-self: flex-start; font: inherit; font-size: 12px; cursor: pointer; padding: 5px 12px; border-radius: $radius-sm;
  border: 1px solid rgba(var(--accent-primary-rgb), 0.4); color: $accent-primary; background: rgba(var(--accent-primary-rgb), 0.08);
  transition: background-color $transition-fast, border-color $transition-fast;
  &:hover { background: rgba(var(--accent-primary-rgb), 0.16); } }
.attach-kb-btn { margin-top: 2px; }

.attach-parsed { display: flex; flex-direction: column; gap: 6px; }
.attach-parsed details { border: 1px solid $border-light; border-radius: $radius-sm; background: $code-bg; }
.attach-parsed summary { cursor: pointer; font-size: 12px; color: $text-secondary; padding: 6px 10px; user-select: none; }
.attach-parsed-text { max-height: 260px; overflow: auto; margin: 0; padding: 8px 10px; font-family: $font-code; font-size: 11.5px;
  white-space: pre-wrap; word-break: break-word; color: $text-secondary; border-top: 1px solid $border-light; }

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulse { 0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); } 40% { opacity: 1; transform: scale(1); } }

.image-preview-overlay { position: fixed; inset: 0; z-index: 9999; background: rgba(0, 0, 0, 0.85);
  display: flex; align-items: center; justify-content: center; padding: 32px; cursor: zoom-out;
  .image-preview-img { max-width: 92vw; max-height: 92vh; object-fit: contain; border-radius: $radius-sm; box-shadow: 0 8px 40px rgba(0, 0, 0, 0.5); cursor: zoom-out; }
  .image-preview-close { position: absolute; top: 18px; right: 22px; width: 38px; height: 38px; border-radius: 50%;
    border: none; background: rgba(255, 255, 255, 0.12); color: #fff; font-size: 24px; line-height: 1; cursor: pointer;
    transition: background-color $transition-fast; &:hover { background: rgba(255, 255, 255, 0.24); } } }

@media (max-width: $breakpoint-mobile) {
  .message.user .msg-body, .message.assistant .msg-body, .message.system .msg-body { max-width: 100%; }
}
</style>
