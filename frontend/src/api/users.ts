/**
 * 用户管理 API（管理员）。
 */
import { request } from './http'
import type { UserItem, RoleItem, UsersResp, RolesResp } from './types'

// 用户管理属于「当前登录管理员」自身的操作，不带 X-Act-As，
// 防止 admin 代管普通用户时因目标用户无 users:read/write 权限而失败。
export const listUsers = () => request<UsersResp>('/api/v1/users', { skipActAs: true })
export const listRoles = () => request<RolesResp>('/api/v1/users/roles', { skipActAs: true })
export const createUser = (body: any) =>
  request<any>('/api/v1/users', { method: 'POST', body, skipActAs: true })
export const updateUser = (uid: number, body: any) =>
  request<any>(`/api/v1/users/${uid}`, { method: 'PUT', body, skipActAs: true })
export const resetUserPassword = (uid: number, password: string) =>
  request<any>(`/api/v1/users/${uid}/password`, { method: 'POST', body: { password }, skipActAs: true })
export const deleteUser = (uid: number) =>
  request<any>(`/api/v1/users/${uid}`, { method: 'DELETE', skipActAs: true })
/** 管理员强制下线：抬高目标用户 password_epoch，使其全部既有令牌立即失效 */
export const revokeUser = (uid: number) =>
  request<any>(`/api/v1/users/${uid}/revoke`, { method: 'POST', skipActAs: true })

export const usersApi = {
  listUsers,
  listRoles,
  createUser,
  updateUser,
  resetUserPassword,
  deleteUser,
  revokeUser,
}
