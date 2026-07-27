/**
 * 鉴权相关 API（对标 hermes-web-ui 的 api/hermes/auth.ts）。
 */
import { request } from './http'
import type { AuthStatus, LoginResp, ChangePasswordReq } from './types'

export const authStatus = () => request<AuthStatus>('/api/v1/auth/status')
export const login = (username: string, password: string) =>
  request<LoginResp>('/api/v1/auth/login', { method: 'POST', body: { username, password } })
export const me = () => request<any>('/api/v1/auth/me')
export const changePassword = (body: ChangePasswordReq) =>
  request<any>('/api/v1/auth/change-password', { method: 'POST', body })

export const authApi = { authStatus, login, me, changePassword }
