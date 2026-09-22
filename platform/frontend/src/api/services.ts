import { apiClient } from './client'

/** 服务分类，与 manifest.yaml 的 category 枚举一致 */
export type ServiceCategory = 'time' | 'file-share' | 'log-monitor'

/** 配置生效方式：hot = 执行 /reload.sh 热重载；restart = 重启容器生效 */
export type ReloadMode = 'hot' | 'restart'

/** 与后端 Service Registry 返回的 manifest 一一对应 */
export interface ServiceSummary {
  name: string
  display_name: string
  category: ServiceCategory
  description: string | null
  container_name: string
  ports: ServicePort[]
  reload_mode: ReloadMode
  /** 当前生效的故障注入模式；未保存过配置时为 null。首页据此列出非正常模式的服务 */
  fault_mode?: string | null
}

export interface ServicePort {
  port: number
  protocol: 'tcp' | 'udp'
  description: string | null
}

/** 字段分组类型：基础配置 vs BMC 故障注入 */
export type FieldGroup = 'base' | 'fault'

/** schema.json 中单个配置字段的描述，驱动前端动态表单 */
export interface ServiceField {
  name: string
  type: 'string' | 'integer' | 'boolean' | 'enum' | 'list' | 'text'
  label: string
  default?: unknown
  required?: boolean
  help?: string
  options?: string[]
  pattern?: string
  min?: number
  max?: number
  item_pattern?: string
  secret?: boolean
  group?: FieldGroup
  pem?: boolean
}

interface ServiceManifest extends ServiceSummary {
  config_dir: string
  config_files: string[]
  data_dir?: string | null
}

interface ServiceSchema {
  fields: ServiceField[]
}

/** 当前生效/已保存的配置；values 为 null 表示尚无已渲染配置 */
export interface ServiceConfig {
  values: Record<string, unknown> | null
  applied: boolean | null
  rendered_at: string | null
}

/** GET /services/{name} 的返回体 */
interface ServiceDetailResponse {
  manifest: ServiceManifest
  schema: ServiceSchema
  config: ServiceConfig
}

interface UpdateConfigResponse {
  message: string
  applied: boolean
}

export interface ServiceStatusResponse {
  name: string
  running: boolean
  health: string | null
  status: string | null
}

export type ServiceAction = 'start' | 'stop' | 'restart'

interface ServiceLogsResponse {
  logs: string
}

/** 获取服务总览列表，后端以 { data, count } 包裹 */
export async function listServices(): Promise<ServiceSummary[]> {
  const { data } = await apiClient.get<{
    data: ServiceSummary[]
    count: number
  }>('/api/v1/services/')
  return data.data
}

export async function getService(name: string): Promise<ServiceDetailResponse> {
  const { data } = await apiClient.get<ServiceDetailResponse>(
    `/api/v1/services/${name}`
  )
  return data
}

export async function updateServiceConfig(
  name: string,
  values: Record<string, unknown>
): Promise<UpdateConfigResponse> {
  const { data } = await apiClient.put<UpdateConfigResponse>(
    `/api/v1/services/${name}/config`,
    { values }
  )
  return data
}

/** 服务生命周期操作，action 直接对应后端子路径 start/stop/restart */
export async function serviceAction(
  name: string,
  action: ServiceAction
): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    `/api/v1/services/${name}/${action}`
  )
  return data
}

export async function getServiceStatus(
  name: string
): Promise<ServiceStatusResponse> {
  const { data } = await apiClient.get<ServiceStatusResponse>(
    `/api/v1/services/${name}/status`
  )
  return data
}

/** 拉取容器最近日志文本
 * @param tail - 末尾行数，默认 200，与后端 query 参数一致
 */
export async function getServiceLogs(
  name: string,
  tail = 200
): Promise<ServiceLogsResponse> {
  const { data } = await apiClient.get<ServiceLogsResponse>(
    `/api/v1/services/${name}/logs`,
    { params: { tail } }
  )
  return data
}

interface ServiceDataEntry {
  name: string
  type: 'file' | 'dir'
  size: number
  modified: string | null
}

interface ServiceDataTree {
  path: string
  entries: ServiceDataEntry[]
}

interface ServiceDataContent {
  path: string
  size: number
  truncated: boolean
  lines: string[]
}

/** 浏览服务数据卷内的文件目录结构 */
export async function getServiceDataTree(
  name: string,
  subpath = ''
): Promise<ServiceDataTree> {
  const { data } = await apiClient.get<ServiceDataTree>(
    `/api/v1/services/${name}/data/tree`,
    { params: { subpath } }
  )
  return data
}

/** 读取服务数据卷内具体日志/数据文件的内容，支持尾部行数裁剪与关键字过滤 */
export async function getServiceDataContent(
  name: string,
  subpath: string,
  tail = 500,
  keyword = ''
): Promise<ServiceDataContent> {
  const { data } = await apiClient.get<ServiceDataContent>(
    `/api/v1/services/${name}/data/content`,
    { params: { subpath, tail, keyword } }
  )
  return data
}
