import { useQuery } from '@tanstack/react-query'
import { listAuditLogs } from '@/api/audit'

/**
 * 首页「最近变更」卡的数据源。
 *
 * 审计接口限 admin，非管理员发起必然 403；所以调用方要按角色传 enabled，
 * 用「不发请求」而不是「请求失败后隐藏」来降级，避免全局错误处理弹出无意义的报错。
 */
export function useAuditLogsQuery(enabled: boolean, limit = 8) {
  return useQuery({
    queryKey: ['audit-logs', limit],
    queryFn: () => listAuditLogs(limit),
    enabled,
  })
}
