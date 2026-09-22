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

/** 审计查询条件；过滤在服务端完成——审计表只增不减，本地过滤只能看到当前页 */
export interface AuditLogQuery {
  offset?: number
  limit?: number
  /** 精确匹配动作名，取值来自 listAuditActions */
  action?: string
  /** 精确匹配服务名 */
  serviceName?: string
  /** 关键字，模糊匹配操作者邮箱与 detail */
  q?: string
}

/** 一页审计日志与「当前过滤条件下的总数」（分页器据此算总页数） */
interface AuditLogsPage {
  data: AuditLogEntry[]
  count: number
}

/**
 * 分页查询审计日志（新事件在前）。
 *
 * 接口要求 admin，非管理员调用会拿到 403，所以调用方必须按角色决定是否发起请求
 * （见 features/home 的「最近变更」卡与 features/audit 页面），不能靠请求失败来兜底。
 *
 * @param query - 分页与过滤条件，缺省取第一页
 * @returns 当前条件下的总数与日志列表
 */
export async function listAuditLogs(
  query: AuditLogQuery = {}
): Promise<AuditLogsPage> {
  const { offset = 0, limit = 100, action, serviceName, q } = query
  const { data } = await apiClient.get<AuditLogsPage>('/api/v1/audit-logs', {
    params: {
      offset,
      limit,
      // 空串代表「不筛选」，不能当过滤值传给后端（否则会筛出 service_name 为空的行）
      ...(action ? { action } : {}),
      ...(serviceName ? { service_name: serviceName } : {}),
      ...(q ? { q } : {}),
    },
  })
  return data
}

/**
 * 库中出现过的全部动作名（升序），作为筛选下拉的选项。
 *
 * 取自实际数据而不是前端写死枚举：动作名随功能演进增减，写死会让新增动作
 * 在筛选器里查不到。
 */
export async function listAuditActions(): Promise<string[]> {
  const { data } = await apiClient.get<{ data: string[] }>(
    '/api/v1/audit-logs/actions'
  )
  return data.data
}
