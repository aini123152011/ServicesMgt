import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
  return useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      updateServiceConfig(name, values),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['services', name] })
      if (res.applied) {
        toast.success(res.message)
      } else {
        toast.warning('已保存，容器未运行，启动后生效')
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
