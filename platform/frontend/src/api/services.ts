import { apiClient } from './client'

/** 服务分类，与 manifest.yaml 的 category 枚举一致 */
export type ServiceCategory = 'time' | 'file-share' | 'log-monitor' | 'network'

/** 配置生效方式：hot = 执行 /reload.sh 热重载；restart = 重启容器生效 */
export type ReloadMode = 'hot' | 'restart'

/** 与后端 Service Registry 返回的 manifest 一一对应 */
export interface ServiceSummary {
  name: string
  display_name: string
  /** 英文展示名；英文界面优先用它，缺失时回落 display_name（字段 label/help 仍是中文） */
  display_name_en?: string | null
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

/** manifest.yaml 的 usage 条目：外部设备/客户端怎么接入本服务 */
export interface ServiceUsageEntry {
  target: string
  summary: string
  command: string
}

interface ServiceManifest extends ServiceSummary {
  config_dir: string
  config_files: string[]
  data_dir?: string | null
  /** 外部使用方式提示；未声明的服务不渲染该卡片 */
  usage?: ServiceUsageEntry[] | null
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
  /**
   * 该服务在二层测试网段上应被 BMC 访问的地址（未启用二层或取不到时为 null）。
   * 「使用方式」卡片用它替换 {{host}}：BMC 在测试网段上够不到管理网地址。
   */
  l2_address?: string | null
}

interface UpdateConfigResponse {
  message: string
  applied: boolean
}

/** 配置历史条目：不含配置内容，看内容用版本详情 */
export interface ServiceConfigVersion {
  id: string
  version: number
  applied: boolean
  /** 渲染产物摘要（sha256 前 16 位），用于判断两次下发产物是否一致 */
  rendered_digest: string
  user_email: string | null
  /** 回滚产生的版本会记下来源版本号 */
  rolled_back_from: number | null
  created_at: string | null
}

/** 版本详情：values 已由后端按 schema 脱敏 */
interface ServiceConfigVersionDetail {
  version: number
  values: Record<string, unknown>
  applied: boolean
  rendered_digest: string
  user_email: string | null
  rolled_back_from: number | null
  created_at: string | null
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

/** 列出该服务的配置版本，版本号倒序（新版本在前） */
export async function listServiceConfigVersions(
  name: string,
  limit = 50
): Promise<ServiceConfigVersion[]> {
  const { data } = await apiClient.get<{
    data: ServiceConfigVersion[]
    count: number
  }>(`/api/v1/services/${name}/config/versions`, { params: { limit } })
  return data.data
}

/** 查看某个版本的配置内容（secret 字段为掩码，后端脱敏） */
export async function getServiceConfigVersion(
  name: string,
  version: number
): Promise<ServiceConfigVersionDetail> {
  const { data } = await apiClient.get<ServiceConfigVersionDetail>(
    `/api/v1/services/${name}/config/versions/${version}`
  )
  return data
}

/** 回滚到指定版本；回滚本身也是一次新下发，会再产生一个版本 */
export async function rollbackServiceConfigVersion(
  name: string,
  version: number
): Promise<UpdateConfigResponse> {
  const { data } = await apiClient.post<UpdateConfigResponse>(
    `/api/v1/services/${name}/config/versions/${version}/rollback`
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
