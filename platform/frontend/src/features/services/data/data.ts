import {
  FileClock,
  FolderOpen,
  ScrollText,
  type LucideIcon,
} from 'lucide-react'
import { type ReloadMode, type ServiceCategory } from '@/api/services'

/** 分类 Badge 的样式映射，以类型为 key 保证新增分类必有样式 */
export const categoryTypes: Record<ServiceCategory, string> = {
  time: 'bg-sky-100/40 text-sky-900 dark:text-sky-100 border-sky-300',
  'file-share':
    'bg-violet-100/40 text-violet-900 dark:text-violet-100 border-violet-300',
  'log-monitor':
    'bg-amber-100/40 text-amber-900 dark:text-amber-100 border-amber-300',
}

export const categoryLabels: Record<ServiceCategory, string> = {
  time: '时间同步',
  'file-share': '文件共享',
  'log-monitor': '日志监控',
}

/** 分类图标，总览卡片空态/详情页头部复用 */
export const categoryIcons: Record<ServiceCategory, LucideIcon> = {
  time: FileClock,
  'file-share': FolderOpen,
  'log-monitor': ScrollText,
}

export const reloadModeLabels: Record<ReloadMode, string> = {
  hot: '热重载生效',
  restart: '重启容器生效',
}
