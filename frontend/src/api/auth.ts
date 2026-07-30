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

export const authApi = { authStatus, login, me, changePassword }
