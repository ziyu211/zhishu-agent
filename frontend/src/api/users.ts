/**
 * 用户管理 API（管理员）。
 */
import { request } from './http'
import type { UserItem, RoleItem, UsersResp, RolesResp } from './types'

export const listUsers = () => request<UsersResp>('/api/v1/users')
export const listRoles = () => request<RolesResp>('/api/v1/users/roles')
export const createUser = (body: any) => request<any>('/api/v1/users', { method: 'POST', body })
export const updateUser = (uid: number, body: any) =>
  request<any>(`/api/v1/users/${uid}`, { method: 'PUT', body })
export const resetUserPassword = (uid: number, password: string) =>
  request<any>(`/api/v1/users/${uid}/password`, { method: 'POST', body: { password } })
export const deleteUser = (uid: number) =>
  request<any>(`/api/v1/users/${uid}`, { method: 'DELETE' })

export const usersApi = {
  listUsers,
  listRoles,
  createUser,
  updateUser,
  resetUserPassword,
  deleteUser,
}
