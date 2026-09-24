import { type UserPublic } from './auth'
import { apiClient } from './client'

/** 后端预置角色名，与 rbac 模块的角色枚举一致 */
export const ROLE_NAMES = ['admin', 'operator', 'readonly'] as const

export type RoleName = (typeof ROLE_NAMES)[number]

/** 后端通用 Message 返回体 */
interface Message {
  message?: string | null
  detail?: string | null
}

/** POST /users 请求体；roles 缺省时后端落 ["readonly"] */
export interface UserCreatePayload {
  email: string
  password: string
  full_name?: string
  roles?: RoleName[]
}

/** PATCH /users/{id} 请求体，只传需要修改的字段 */
export interface UserUpdatePayload {
  full_name?: string | null
  email?: string
  password?: string
  is_active?: boolean
  roles?: RoleName[]
}

/** PATCH /users/me 请求体：只改自身资料，角色与启用状态不在此接口范围内 */
export interface UserSelfUpdatePayload {
  full_name?: string | null
  email?: string
}

/** PATCH /users/me/password 请求体；后端要求两个字段都不短于 8 位 */
export interface PasswordUpdatePayload {
  current_password: string
  new_password: string
}

/** GET /users?skip&limit，后端以 { data, count } 包裹；当前一次全量拉取、前端本地分页 */
export async function listUsers(skip = 0, limit = 100): Promise<UserPublic[]> {
  const { data } = await apiClient.get<{
    data: UserPublic[]
    count: number
  }>('/api/v1/users/', { params: { skip, limit } })
  return data.data
}

export async function createUser(
  payload: UserCreatePayload
): Promise<UserPublic> {
  const { data } = await apiClient.post<UserPublic>('/api/v1/users/', payload)
  return data
}

export async function updateUser(
  id: string,
  payload: UserUpdatePayload
): Promise<UserPublic> {
  const { data } = await apiClient.patch<UserPublic>(
    `/api/v1/users/${id}`,
    payload
  )
  return data
}

/** DELETE /users/{id}，成功返回 Message */
/**
 * 管理员手动把账号标记为「邮箱已验证」。
 *
 * 用于用户收不到验证邮件时的人工放行；幂等，已验证过再调用不会重复写审计。
 */
export async function verifyUserEmail(id: string): Promise<UserPublic> {
  const { data } = await apiClient.post<UserPublic>(
    `/api/v1/users/${id}/verify-email`
  )
  return data
}

export async function deleteUser(id: string): Promise<Message> {
  const { data } = await apiClient.delete<Message>(`/api/v1/users/${id}`)
  return data
}

/**
 * 修改当前登录用户的姓名/邮箱。
 * 邮箱与他人重复时后端返回 409，由全局错误处理展示。
 */
export async function updateCurrentUser(
  payload: UserSelfUpdatePayload
): Promise<UserPublic> {
  const { data } = await apiClient.patch<UserPublic>(
    '/api/v1/users/me',
    payload
  )
  return data
}

/** 修改当前登录用户的密码；当前密码错误或新旧相同时后端返回 400 */
export async function updateCurrentUserPassword(
  payload: PasswordUpdatePayload
): Promise<Message> {
  const { data } = await apiClient.patch<Message>(
    '/api/v1/users/me/password',
    payload
  )
  return data
}

/** GET /roles 角色字典，仅 admin 可调 */
export async function listRoles(): Promise<string[]> {
  const { data } = await apiClient.get<{ data: string[] }>('/api/v1/roles')
  return data.data
}
