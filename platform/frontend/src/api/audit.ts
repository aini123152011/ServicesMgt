import { apiClient } from './client'

/** 与后端 app/models.py 的 AuditLogPublic 一一对应（不外露 user_id，操作者靠 user_email 追溯） */
export interface AuditLogEntry {
  id: string
  user_email: string | null
  action: string
  /** 服务操作记服务名；用户操作留空 */
  service_name: string | null
  detail: string | null
  created_at: string | null
}

/**
 * 最近审计日志（新事件在前）。
 *
 * 接口要求 admin，非管理员调用会拿到 403，所以调用方必须按角色决定是否发起请求
 * （见 features/home 的「最近变更」卡），不能靠请求失败来兜底。
 *
 * @param limit - 单页条数，首页只取最近几条
 */
export async function listAuditLogs(limit = 8): Promise<AuditLogEntry[]> {
  const { data } = await apiClient.get<{
    data: AuditLogEntry[]
    count: number
  }>('/api/v1/audit-logs', { params: { limit } })
  return data.data
}
