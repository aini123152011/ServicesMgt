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
