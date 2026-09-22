import z from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { AuditLogs } from '@/features/audit'

/**
 * 审计页的查询条件全部放在 URL 上：分页与筛选是服务端行为，
 * 刷新页面或把链接发给同事后应看到同一批结果。
 */
const auditSearchSchema = z.object({
  page: z.number().int().min(1).optional().catch(1),
  // 上限与后端 MAX_LIST_LIMIT（500）保持一致，避免构造出必然 422 的链接
  pageSize: z.number().int().min(1).max(500).optional().catch(20),
  action: z.string().optional().catch(''),
  service: z.string().optional().catch(''),
  q: z.string().optional().catch(''),
})

export const Route = createFileRoute('/_authenticated/audit/')({
  validateSearch: auditSearchSchema,
  component: AuditLogs,
})
