import { apiClient } from './client'

export type AccessRuleKind = 'email_suffix' | 'ip'
export type AccessRuleListType = 'allow' | 'deny'

/** 与后端 AccessRulePublic 一一对应 */
export interface AccessRule {
  id: string
  kind: AccessRuleKind
  list_type: AccessRuleListType
  /** 归一化后的值：邮箱后缀小写去 @；IP 压成网段形式 */
  value: string
  note: string | null
  created_at: string | null
}

interface AccessControlSettings {
  registration_enabled: boolean
  /** 服务端邮件是否已配置；未配置时自助注册不可用（注册要发验证码） */
  email_configured: boolean
}

export interface AccessRulePayload {
  kind: AccessRuleKind
  list_type: AccessRuleListType
  value: string
  note: string | null
}

export async function listAccessRules(
  kind?: AccessRuleKind
): Promise<AccessRule[]> {
  const { data } = await apiClient.get<{ data: AccessRule[]; count: number }>(
    '/api/v1/access-control/rules/',
    { params: kind ? { kind } : undefined }
  )
  return data.data
}

/**
 * 新建规则。
 *
 * IP 规则会先在服务端做防自锁自检：若新规则会让**当前请求 IP** 无法登录，
 * 后端直接拒绝保存并返回原因（400），错误由全局拦截器展示。
 */
export async function createAccessRule(
  payload: AccessRulePayload
): Promise<AccessRule> {
  const { data } = await apiClient.post<AccessRule>(
    '/api/v1/access-control/rules/',
    payload
  )
  return data
}

export async function deleteAccessRule(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/access-control/rules/${id}`)
}

export async function getAccessControlSettings(): Promise<AccessControlSettings> {
  const { data } = await apiClient.get<AccessControlSettings>(
    '/api/v1/access-control/settings/'
  )
  return data
}

export async function updateAccessControlSettings(payload: {
  registration_enabled: boolean
}): Promise<AccessControlSettings> {
  const { data } = await apiClient.patch<AccessControlSettings>(
    '/api/v1/access-control/settings/',
    payload
  )
  return data
}
