import { apiClient } from './client'

/** 更新目标：平台自身（target="platform"）或某个服务（target=服务名） */
export interface UpdateTarget {
  target: string
  display_name: string
  container_name: string
  image: string
  running_image_id: string | null
  available_image_id: string | null
  image_created: string | null
  container_running: boolean
  update_available: boolean
}

/** 最近一次更新任务状态（后端写文件持久化，平台自更新重启后仍可读） */
export interface UpdateTaskState {
  status: 'idle' | 'running' | 'succeeded' | 'failed'
  phase?: string | null
  target?: string | null
  image?: string | null
  message?: string | null
  updated_at?: string | null
  finished_at?: string | null
}

export interface SystemInfo {
  version: string
  build: string
  update_registry: string
  /** Docker 不可达时为 false（targets 为空） */
  docker_available: boolean
  status: UpdateTaskState | null
  targets: UpdateTarget[]
}

interface UpdateCheckResult {
  registry: string
  targets: UpdateTarget[]
  update_available: string[]
}

export async function getSystemInfo(): Promise<SystemInfo> {
  const { data } = await apiClient.get<SystemInfo>('/api/v1/system/info')
  return data
}

export async function checkUpdates(): Promise<UpdateCheckResult> {
  const { data } = await apiClient.post<UpdateCheckResult>(
    '/api/v1/system/updates/check'
  )
  return data
}

/** 上传离线镜像包（tar）；后端 docker load 后返回最新的检查结果 */
export async function uploadUpdatePackage(
  file: File
): Promise<UpdateCheckResult> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await apiClient.post<UpdateCheckResult>(
    '/api/v1/system/updates/package',
    form
  )
  return data
}

export async function applyUpdate(
  target: string,
  image: string
): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    '/api/v1/system/updates/apply',
    { target, image }
  )
  return data
}

export async function getUpdateStatus(): Promise<UpdateTaskState> {
  const { data } = await apiClient.get<UpdateTaskState>(
    '/api/v1/system/updates/status'
  )
  return data
}

/** 宿主网口的 IPv4 地址（cidr 已归一，便于展示与网段比对） */
export interface HostIPv4Address {
  address: string
  netmask: string
  cidr: string
}

/** 宿主网口的 IPv6 地址（链路本地已过滤） */
export interface HostIPv6Address {
  address: string
  prefix: number
  cidr: string
}

/** 宿主物理网口：carrier=1 才说明插了线（UP 不等于插了线） */
export interface HostInterface {
  name: string
  carrier: number | null
  speed_mbps: number | null
  mac: string | null
  ipv4: HostIPv4Address[]
  ipv6: HostIPv6Address[]
}

/** 服务当前挂的二层绑定（macvlan 网络与它的 parent 网口） */
export interface HostServiceBinding {
  service: string
  container: string
  network: string | null
  parent: string | null
  attached: boolean
  /** 容器在该 macvlan 网络上的地址 */
  address: string | null
}

/** 一条一致性校验结论：level 为 ok/info/warn/error */
export interface HostNetworkCheck {
  level: 'ok' | 'info' | 'warn' | 'error'
  code: string
  service: string | null
  message: string
}

export interface HostNetworkInfo {
  /** ok=已读到宿主各口 IP；unavailable=helper 容器不可用（IP 留空，不算失败） */
  ip_source: string
  parent_iface: string
  l2_subnet: string
  l2_services: string[]
  /** 宿主默认路由出口网口：与 parent_iface 相同说明测试口承载了默认路由 */
  default_iface: string
  interfaces: HostInterface[]
  bindings: HostServiceBinding[]
  checks: HostNetworkCheck[]
}

export async function getHostNetwork(): Promise<HostNetworkInfo> {
  const { data } = await apiClient.get<HostNetworkInfo>(
    '/api/v1/system/host-network'
  )
  return data
}
