import { apiClient } from './client'
import { type HostNetworkCheck } from './system'

/** 可作为 macvlan 父口的宿主网口（含「为什么不能选」的原因） */
export interface L2CandidateInterface {
  name: string
  carrier: number | null
  speed_mbps: number | null
  mac: string | null
  addresses: string[]
  is_parent: boolean
  selectable: boolean
  reason: string
}

/** 二层夹具状态：.env 权威值 + Docker 实际值 + 校验结论 */
export interface L2State {
  enabled: boolean
  env_path: string
  /** 部署目录没挂载时为 false（此时用容器环境变量兜底，页面要提示） */
  env_available: boolean
  env_error: string
  parent_iface: string
  l2_subnet: string
  l2_gateway: string
  l2_subnet_v6: string
  l2_gateway_v6: string
  l2_services: string[]
  /** .env 里重复出现的键（后者生效） */
  duplicate_keys: string[]
  network: string | null
  network_exists: boolean
  network_parent: string | null
  attached: boolean
  address: string | null
  address_v6: string | null
  /** .env 与 Docker 实际值不一致的说明 */
  drift: string[]
  checks: HostNetworkCheck[]
  candidates: L2CandidateInterface[]
  nmcli_commands: string[]
  default_iface: string
}

export interface L2ConfigPayload {
  parent_iface: string
  l2_subnet: string
  l2_gateway: string
  l2_subnet_v6: string
  l2_gateway_v6: string
  /** 是否同时把 dhcp 服务配置（网关 / RA 前缀 / 地址池）改成匹配值 */
  sync_service_config: boolean
}

/** 预检响应：只被 preflightL2Config 的返回类型用到，不对外导出（knip 会报未使用导出） */
interface L2PreflightResult {
  ok: boolean
  blocking: HostNetworkCheck[]
  checks: HostNetworkCheck[]
  steps: string[]
  service_config: Record<string, string>
  nmcli_commands: string[]
}

export interface L2ServiceConfigChange {
  changed: Record<string, string>
  applied: boolean
  version: number | null
}

export interface L2ApplyResult {
  applied: boolean
  rolled_back: boolean
  steps: string[]
  service_config: L2ServiceConfigChange | null
  message: string
}

export async function getL2Status(): Promise<L2State> {
  const { data } = await apiClient.get<L2State>('/api/v1/l2/status')
  return data
}

/** 干跑一次变更：不产生任何变更，用于确认弹窗展示将要改什么 */
export async function preflightL2Config(
  payload: L2ConfigPayload
): Promise<L2PreflightResult> {
  const { data } = await apiClient.post<L2PreflightResult>(
    '/api/v1/l2/preflight',
    payload
  )
  return data
}

/** 启用或切换：写 .env、重建 macvlan 网络、重启 dhcp、可选联动服务配置 */
export async function applyL2Config(
  payload: L2ConfigPayload
): Promise<L2ApplyResult> {
  const { data } = await apiClient.put<L2ApplyResult>(
    '/api/v1/l2/config',
    payload
  )
  return data
}

/** 停用：断开容器、删除网络、清空 .env 的父口 */
export async function disableL2(): Promise<L2ApplyResult> {
  const { data } = await apiClient.post<L2ApplyResult>('/api/v1/l2/disable')
  return data
}
