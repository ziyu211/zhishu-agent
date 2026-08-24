<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NButton } from 'naive-ui'
import { useChatStore, type PendingAttachment } from '@/stores/chat'
import { toast } from '@/api/client'
import { confirmDialog } from '@/api/http'

const chat = useChatStore()
const inputText = ref('')
const textareaRef = ref<HTMLTextAreaElement>()
const fileInputRef = ref<HTMLInputElement>()
const isComposing = ref(false)
const sending = ref(false)

// 待发送附件（借鉴 hermes-web-ui：先入托盘，发送时统一提交，支持多选 / 拖拽 / 粘贴）
const pendingFiles = ref<PendingAttachment[]>([])
const dragCounter = ref(0)
const isDragging = ref(false)

const MAX_MB = 8
const isImage = (type: string) => type.startsWith('image/')

const canSend = () =>
  !sending.value && (inputText.value.trim().length > 0 || pendingFiles.value.length > 0)

function formatSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// ---------- 附件入托盘（点击 / 拖拽 / 粘贴 三入口统一） ----------
function addFile(file: File) {
  if (file.size > MAX_MB * 1024 * 1024) {
    toast('warning', `文件过大，请控制在 ${MAX_MB}MB 以内`)
    return
  }
  if (pendingFiles.value.find((a) => a.name === file.name)) {
    toast('warning', `「${file.name}」已在待发送列表`)
    return
  }
  const id = 'f_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
  const url = URL.createObjectURL(file)
  pendingFiles.value.push({
    id,
    name: file.name,
    type: file.type || 'application/octet-stream',
    size: file.size,
    url,
    file,
  })
}

function triggerAttach() {
  fileInputRef.value?.click()
}

function handleFileChange(e: Event) {
  const el = e.target as HTMLInputElement
  if (el.files) for (const f of Array.from(el.files)) addFile(f)
  el.value = '' // 允许重复选择同一文件
}

// 拖拽（用 dragCounter 解决子元素 dragleave 抖动）
function handleDragOver(e: DragEvent) {
  // 仅对文件拖拽阻止默认行为，保留 textarea 内普通文本拖拽/选中的默认交互
  if (e.dataTransfer?.types.includes('Files')) {
    e.preventDefault()
  }
}
function handleDragEnter(e: DragEvent) {
  if (e.dataTransfer?.types.includes('Files')) {
    e.preventDefault()
    dragCounter.value++
    isDragging.value = true
  }
}
function handleDragLeave() {
  dragCounter.value = Math.max(0, dragCounter.value - 1)
  if (dragCounter.value === 0) isDragging.value = false
}
function handleDrop(e: DragEvent) {
  e.preventDefault()
  dragCounter.value = 0
  isDragging.value = false
  const files = Array.from(e.dataTransfer?.files || [])
  for (const f of files) addFile(f)
  textareaRef.value?.focus()
}

function removeFile(id: string) {
  const idx = pendingFiles.value.findIndex((a) => a.id === id)
  if (idx === -1) return
  const [removed] = pendingFiles.value.splice(idx, 1)
  if (removed?.url) URL.revokeObjectURL(removed.url)
}

// 支持粘贴图片
function handlePaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  let handled = false
  for (const it of Array.from(items)) {
    if (it.type.startsWith('image/')) {
      const blob = it.getAsFile()
      if (blob) {
        const ext = it.type.split('/')[1] || 'png'
        const file = new File([blob], `pasted-${Date.now()}.${ext}`, { type: it.type })
        addFile(file)
        handled = true
      }
    }
  }
  if (handled) e.preventDefault()
}

// ---------- 发送 ----------
async function handleSend() {
  const text = inputText.value.trim()
  if ((!text && pendingFiles.value.length === 0) || chat.streaming || sending.value) return

  const files = pendingFiles.value.slice()
  if (files.length === 0) {
    // 纯文本
    chat.send(text)
    inputText.value = ''
    if (textareaRef.value) textareaRef.value.style.height = 'auto'
    return
  }

  sending.value = true
  try {
    // 第一步：所有文件仅落盘
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      await chat.sendAttachment(f.file!, { isImage: isImage(f.type) })
    }

    // 第二步：不管有没有文字，自动触发 stream 让 agent 读取文件
    const promptText = text || `请读取并总结以上上传的文件内容。`
    chat.send(promptText)
  } catch (err: any) {
    toast('error', err?.message || '发送失败')
  } finally {
    sending.value = false
    for (const f of files) if (f.url) URL.revokeObjectURL(f.url)
    pendingFiles.value = []
    inputText.value = ''
    if (textareaRef.value) textareaRef.value.style.height = 'auto'
  }
}

function handleCompositionStart() {
  isComposing.value = true
}
function handleCompositionEnd() {
  requestAnimationFrame(() => (isComposing.value = false))
}
function isImeEnter(e: KeyboardEvent): boolean {
  return isComposing.value || e.isComposing || (e as any).keyCode === 229
}
function handleKeydown(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey) return
  if (isImeEnter(e)) return
  e.preventDefault()
  handleSend()
}
function handleInput(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 140) + 'px'
}

onMounted(() => textareaRef.value?.focus())
</script>

<template>
    <div
      class="chat-input-area"
      @dragover="handleDragOver"
      @dragenter="handleDragEnter"
      @dragleave="handleDragLeave"
      @drop="handleDrop"
    >
    <!-- 待发送附件托盘 -->
    <div v-if="pendingFiles.length > 0" class="attachment-previews">
      <div
        v-for="att in pendingFiles"
        :key="att.id"
        class="attachment-preview"
        :class="{ image: isImage(att.type) }"
      >
        <template v-if="isImage(att.type)">
          <img :src="att.url" :alt="att.name" class="attachment-thumb" />
        </template>
        <template v-else>
          <div class="attachment-file">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
            <span class="file-name">{{ att.name }}</span>
            <span class="file-size">{{ formatSize(att.size) }}</span>
          </div>
        </template>
        <button class="attachment-remove" title="移除" @click="removeFile(att.id)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
    </div>

    <div
      class="input-wrapper"
      :class="{ 'drag-over': isDragging }"
    >
      <button class="upload-btn" title="上传文件 / 图片（发到对话框并解析）" @click="triggerAttach">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
      </button>
      <input
        ref="fileInputRef"
        type="file"
        multiple
        class="file-hidden"
        accept="*"
        @change="handleFileChange"
      />

      <textarea
        ref="textareaRef"
        v-model="inputText"
        class="input-textarea"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行；可拖拽 / 粘贴 / 点击 📎 上传文件或图片"
        rows="1"
        @keydown="handleKeydown"
        @paste="handlePaste"
        @compositionstart="handleCompositionStart"
        @compositionend="handleCompositionEnd"
        @input="handleInput"
      ></textarea>
      <div class="input-actions">
        <NButton v-if="chat.streaming" size="small" type="error" @click="chat.stop(chat.activeId)">停止</NButton>
        <NButton v-else size="small" type="primary" :disabled="!canSend() || sending" @click="handleSend">
          <template #icon>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          </template>
          发送
        </NButton>
      </div>
    </div>

    <div v-if="isDragging" class="drag-hint">松开即可添加文件</div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.chat-input-area {
  padding: 12px 24px 18px;
  border-top: 1px solid $border-color;
  flex-shrink: 0;
  position: relative;
}

// ── 待发送附件托盘（借鉴 hermes-web-ui） ──
.attachment-previews {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.attachment-preview {
  position: relative;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  background-color: $bg-card;
  padding: 6px;
  max-width: 200px;
  .dark & {
    background-color: #2a2a2a;
  }
  &.image {
    width: 64px;
    height: 64px;
    padding: 0;
    overflow: hidden;
  }
}
.attachment-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  display: block;
  border-radius: $radius-md;
}
.attachment-file {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 4px;
  color: $text-secondary;
  svg {
    color: $accent-primary;
  }
  .file-name {
    font-size: 12px;
    color: $text-primary;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 170px;
  }
  .file-size {
    font-size: 10px;
    color: $text-muted;
  }
}
.attachment-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
  opacity: 0;
  transition: opacity $transition-fast, background-color $transition-fast;
  &:hover {
    background: rgba(0, 0, 0, 0.85);
  }
}
.attachment-preview:hover .attachment-remove {
  opacity: 1;
}

.input-wrapper {
  display: flex;
  align-items: center;
  gap: 10px;
  background-color: $bg-input;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  padding: 10px 12px;
  transition: border-color $transition-fast, background-color $transition-fast;
  &:focus-within {
    border-color: $accent-primary;
  }
  .dark & {
    background-color: #333333;
  }
  // 拖拽悬停态
  &.drag-over {
    border-style: dashed;
    border-color: $accent-primary;
    background-color: rgba(var(--accent-primary-rgb), 0.06);
  }
}

.upload-btn {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border: none;
  background: none;
  color: $text-muted;
  cursor: pointer;
  border-radius: $radius-sm;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color $transition-fast, background-color $transition-fast;
  &:hover:not(:disabled) {
    color: $accent-primary;
    background-color: rgba(0, 0, 0, 0.04);
  }
  .dark &:hover:not(:disabled) {
    background-color: rgba(255, 255, 255, 0.06);
  }
  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}
.file-hidden {
  display: none;
}

.input-textarea {
  flex: 1;
  background: none;
  border: none;
  outline: none;
  color: $text-primary;
  font-family: $font-ui;
  font-size: 14px;
  line-height: 22px;
  resize: none;
  max-height: 140px;
  min-height: 22px;
  padding: 0;
  overflow-y: auto;
  &::placeholder {
    color: $text-muted;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.input-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  align-items: center;
}

.drag-hint {
  margin-top: 6px;
  font-size: 11px;
  color: $accent-primary;
  text-align: center;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.spin {
  animation: spin 1s linear infinite;
}
</style>
