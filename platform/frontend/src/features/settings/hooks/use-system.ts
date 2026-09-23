import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  applyUpdate,
  checkUpdates,
  getHostNetwork,
  type HostNetworkInfo,
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
 * 宿主网口事实与二层绑定：默认 30s 轮询一次。
 *
 * 轮询是必要的——网口/接线是物理动作，平台无从感知变化；打开着面板时能自动反映最新 carrier 状态。
 * 后端侧对 helper 容器结果另有 30s 缓存，两者叠加不会造成额外的容器开销。
 */
export function useHostNetworkQuery(refetchInterval: number = 30000) {
  return useQuery<HostNetworkInfo>({
    queryKey: ['system', 'host-network'],
    queryFn: getHostNetwork,
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
