import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listAuditLogs } from '@/api/audit'
import { getService, updateServiceConfig } from '@/api/services'

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

/**
 * 复位故障注入：把服务的 fault_mode 改回 none。
 *
 * 先把当前配置整体取回再提交（只改 fault_mode 一项）：配置项之间可能相互依赖，
 * 只提交单字段会丢掉其余字段。secret 字段在详情里是脱敏值，后端提交时会用库中
 * 原值回填，因此整份回传不会把密码写成掩码。
 */
export function useResetFaultModeMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (serviceName: string) => {
      const detail = await getService(serviceName)
      const values = { ...(detail.config.values ?? {}), fault_mode: 'none' }
      return updateServiceConfig(serviceName, values)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services'] })
    },
  })
}
