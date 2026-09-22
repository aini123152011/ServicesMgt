import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  applyUpdate,
  checkUpdates,
  getSystemInfo,
  getUpdateStatus,
  uploadUpdatePackage,
} from '@/api/system'

/**
 * 平台版本与服务镜像现状。
 *
 * 传入 refetchInterval 即为「自动检测更新」：接口只做本地镜像比对（不访问仓库），
 * 顶栏更新入口按 5 分钟一次轮询。
 */
export function useSystemInfoQuery(refetchInterval?: number) {
  return useQuery({
    queryKey: ['system', 'info'],
    queryFn: getSystemInfo,
    refetchInterval,
  })
}

/**
 * 更新任务状态：有任务在跑时 3s 轮询一次，否则不轮询。
 * 平台自更新会让接口短暂不可用，查询失败由全局错误处理接管，恢复后自动继续。
 */
export function useUpdateStatusQuery(enabled: boolean) {
  return useQuery({
    queryKey: ['system', 'update-status'],
    queryFn: getUpdateStatus,
    enabled,
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 3000 : false,
  })
}

/** 检查更新：配置了镜像仓库时后端会先 pull，耗时可能较长 */
export function useCheckUpdatesMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: checkUpdates,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['system', 'info'] })
    },
  })
}

/** 上传离线包：成功后刷新镜像现状与检查结论 */
export function useUploadPackageMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: uploadUpdatePackage,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['system', 'info'] })
    },
  })
}

/** 应用更新：服务后台重建、平台由 helper 容器重建，随后轮询状态 */
export function useApplyUpdateMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ target, image }: { target: string; image: string }) =>
      applyUpdate(target, image),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['system', 'update-status'] })
    },
  })
}
