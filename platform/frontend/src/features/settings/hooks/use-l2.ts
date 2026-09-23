import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  applyL2Config,
  disableL2,
  getL2Status,
  type L2ApplyResult,
  type L2ConfigPayload,
  type L2State,
  preflightL2Config,
} from '@/api/l2'
import { handleServerError } from '@/lib/handle-server-error'

/**
 * 二层夹具状态：默认 30s 轮询。
 *
 * 与宿主网口面板同样要轮询——接线与容器网络是外部动作，打开着页面时应当自动反映最新状态。
 */
export function useL2StatusQuery(refetchInterval: number = 30000) {
  return useQuery<L2State>({
    queryKey: ['l2', 'status'],
    queryFn: getL2Status,
    refetchInterval,
  })
}

/** 预检：干跑，不产生变更；用于确认弹窗里展示将要执行的步骤与配置差异 */
export function useL2PreflightMutation() {
  return useMutation({
    mutationFn: (payload: L2ConfigPayload) => preflightL2Config(payload),
    // 参数非法时后端返回 400：必须提示，否则点了按钮页面毫无反应（实机联调踩到）
    onError: handleServerError,
  })
}

/**
 * 启用 / 切换：成功后刷新二层状态与宿主网口事实。
 *
 * 两个 queryKey 都要失效——变更会同时改写网络（l2/status）与容器连接（system/host-network），
 * 只失效一个会让页面另一半显示旧值。
 */
export function useL2ApplyMutation() {
  const queryClient = useQueryClient()
  return useMutation<L2ApplyResult, Error, L2ConfigPayload>({
    mutationFn: (payload) => applyL2Config(payload),
    onError: handleServerError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['l2', 'status'] })
      queryClient.invalidateQueries({ queryKey: ['system', 'host-network'] })
      queryClient.invalidateQueries({ queryKey: ['services', 'dhcp'] })
    },
  })
}

/** 停用：断开并删除 macvlan 网络，清空 .env 的父口 */
export function useL2DisableMutation() {
  const queryClient = useQueryClient()
  return useMutation<L2ApplyResult, Error, void>({
    mutationFn: () => disableL2(),
    onError: handleServerError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['l2', 'status'] })
      queryClient.invalidateQueries({ queryKey: ['system', 'host-network'] })
    },
  })
}
