import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  getService,
  getServiceDataContent,
  getServiceDataTree,
  getServiceLogs,
  getServiceStatus,
  listServices,
  serviceAction,
  updateServiceConfig,
  type ServiceAction,
} from '@/api/services'

/** 服务总览列表 */
export function useServicesQuery() {
  return useQuery({ queryKey: ['services'], queryFn: listServices })
}

/** 单服务详情（manifest + schema + 当前配置），保存配置后由 mutation 失效 */
export function useServiceQuery(name: string) {
  return useQuery({
    queryKey: ['services', name],
    queryFn: () => getService(name),
  })
}

/**
 * 轮询服务容器状态，5s 一次；页面不可见时 TanStack Query 自动暂停。
 * 错误由全局 QueryCache 统一处理，本 hook 不重复 toast。
 */
export function useServiceStatusQuery(name: string) {
  return useQuery({
    queryKey: ['services', name, 'status'],
    queryFn: () => getServiceStatus(name),
    refetchInterval: 5000,
  })
}

/**
 * 批量轮询多个服务的状态，返回以服务名为键的映射，供首页健康表与统计卡使用。
 *
 * 与单服务 hook 分开而不是循环调用：首页要同时显示全部服务，逐个挂 5s 轮询会让请求数
 * 随服务数量线性膨胀；这里合并成一次并发请求，并把间隔放宽到 15s（首页是总览，不需要秒级）。
 */
export function useServicesStatusQuery(names: string[], intervalMs = 15000) {
  return useQuery({
    queryKey: ['services', 'statuses', names],
    queryFn: async () => {
      const statuses = await Promise.all(
        names.map((name) => getServiceStatus(name))
      )
      return Object.fromEntries(
        statuses.map((status) => [status.name, status] as const)
      )
    },
    enabled: names.length > 0,
    refetchInterval: intervalMs,
  })
}

/** 容器日志，仅在日志弹窗打开时请求，避免无谓流量 */
export function useServiceLogsQuery(name: string, enabled: boolean) {
  return useQuery({
    queryKey: ['services', name, 'logs'],
    queryFn: () => getServiceLogs(name),
    enabled,
  })
}

/** 保存配置；applied=false 表示容器未运行、配置已落盘但需启动后生效 */
export function useUpdateConfigMutation(name: string) {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      updateServiceConfig(name, values),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['services', name] })
      if (res.applied) {
        // 成功文案来自后端（本轮保持中文，见 design.md §3.3）
        toast.success(res.message)
      } else {
        toast.warning(t('services.config.savedNotApplied'))
      }
    },
    // 失败走 main.tsx 的全局 mutation onError，不在此重复处理
  })
}

/** 生命周期操作；成功后同时失效状态轮询与总览列表 */
export function useServiceActionMutation(name: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (action: ServiceAction) => serviceAction(name, action),
    onSuccess: (res) => {
      // 后端返回的操作结果文案（本轮保持中文）
      toast.success(res.message)
      queryClient.invalidateQueries({
        queryKey: ['services', name, 'status'],
      })
      queryClient.invalidateQueries({ queryKey: ['services'] })
    },
  })
}

/** 数据卷目录树查询 */
export function useServiceDataTreeQuery(
  name: string,
  subpath = '',
  enabled = true
) {
  return useQuery({
    queryKey: ['services', name, 'data', 'tree', subpath],
    queryFn: () => getServiceDataTree(name, subpath),
    enabled,
  })
}

/** 数据卷文件内容查询（按尾部行数与关键词过滤） */
export function useServiceDataContentQuery(
  name: string,
  subpath: string,
  tail = 500,
  keyword = '',
  enabled = true
) {
  return useQuery({
    queryKey: ['services', name, 'data', 'content', subpath, tail, keyword],
    queryFn: () => getServiceDataContent(name, subpath, tail, keyword),
    enabled: enabled && !!subpath,
  })
}
