/**
 * 管理员「切换用户（代管）」全局状态。
 * 仅 admin 可用；设置后所有请求自动携带 X-Act-As 头，后端据此以目标用户身份执行，
 * 从而实现「以某用户视角查看 / 配置其私有模块」。空字符串表示以自己的身份操作。
 */
import { ref } from 'vue'

export const actAs = ref<string>('')

export function setActAs(u: string) {
  actAs.value = u || ''
}

export function getActAs(): string {
  return actAs.value || ''
}

export function clearActAs() {
  actAs.value = ''
}
