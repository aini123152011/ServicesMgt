import { apiClient } from './client'

/** 后端 POST /login/access-token 的返回体 */
export interface Token {
  access_token: string
  token_type: string
}

/** 与后端 app/models.py 的 UserPublic 一一对应 */
export interface UserPublic {
  id: string
  email: string
  is_active: boolean
  is_superuser: boolean
  full_name: string | null
}

/**
 * OAuth2 密码模式登录：按后端约定 username 字段传邮箱。
 * 失败时后端返回 400 { detail }，错误原样上抛由调用方展示。
 */
export async function loginAccessToken(
  email: string,
  password: string
): Promise<Token> {
  const form = new URLSearchParams({ username: email, password })
  const { data } = await apiClient.post<Token>(
    '/api/v1/login/access-token',
    form
  )
  return data
}

/** 获取当前登录用户，依赖请求拦截器已附带 Bearer token */
export async function getCurrentUser(): Promise<UserPublic> {
  const { data } = await apiClient.get<UserPublic>('/api/v1/users/me')
  return data
}
