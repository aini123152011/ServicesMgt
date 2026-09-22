import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  listAuditActions,
  listAuditLogs,
  type AuditLogQuery,
} from '@/api/audit'

/**
 * 审计日志页的数据源：分页与过滤都交给后端。
 *
 * 审计表只增不减，若像用户列表那样一次全量拉回再本地过滤，翻页与筛选就只能在
 * 已加载的那一页里成立，页数与命中数都会失真。
 *
 * placeholderData 让翻页时保留上一页内容，避免每次翻页整表闪成骨架。
 */
export function useAuditLogsPageQuery(query: AuditLogQuery) {
  return useQuery({
    queryKey: ['audit-logs', 'page', query],
    queryFn: () => listAuditLogs(query),
    placeholderData: keepPreviousData,
  })
}

/** 筛选下拉的动作字典，取自库中实际出现过的动作名 */
export function useAuditActionsQuery() {
  return useQuery({ queryKey: ['audit-actions'], queryFn: listAuditActions })
}
