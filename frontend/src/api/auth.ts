/**
 * 鉴权相关 API（对标 hermes-web-ui 的 api/hermes/auth.ts）。
 */
import { request } from './http'
import type { AuthStatus, LoginResp, ChangePasswordReq } from './types'

export const authStatus = () => request<AuthStatus>('/api/v1/auth/status')
export const login = (username: string, password: string) =>
  request<LoginResp>('/api/v1/auth/login', { method: 'POST', body: { username, password } })
// /auth/me 永远查当前登录用户身份，不带 X-Act-As，避免代管时 isAdmin 被刷成 false
export const me = () => request<any>('/api/v1/auth/me', { skipActAs: true })
export const changePassword = (body: ChangePasswordReq) =>
  request<any>('/api/v1/auth/change-password', { method: 'POST', body, skipActAs: true })
// 主动登出：吊销令牌并清除 /media 鉴权 Cookie（后端 /auth/logout 处理）。
// skipActAs 避免管理员代管身份穿透，导致误吊销目标用户会话。
export const logout = () => request<any>('/api/v1/auth/logout', { method: 'POST', skipActAs: true })

export const authApi = { authStatus, login, me, changePassword, logout }
