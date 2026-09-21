import {
  FileClock,
  FolderOpen,
  ScrollText,
  type LucideIcon,
} from 'lucide-react'
import { type ReloadMode, type ServiceCategory } from '@/api/services'
import { type TranslationKey } from '@/lib/i18n'

/** 分类 Badge 的样式映射，以类型为 key 保证新增分类必有样式 */
export const categoryTypes: Record<ServiceCategory, string> = {
  time: 'bg-sky-100/40 text-sky-900 dark:text-sky-100 border-sky-300',
  'file-share':
    'bg-violet-100/40 text-violet-900 dark:text-violet-100 border-violet-300',
  'log-monitor':
    'bg-amber-100/40 text-amber-900 dark:text-amber-100 border-amber-300',
}

/** 侧边栏菜单里的分类顺序，固定下来避免每次刷新顺序跳动 */
export const categoryOrder: ServiceCategory[] = [
  'time',
  'file-share',
  'log-monitor',
]

/**
 * 分类名与生效方式的文案 key。
 * 这些映射是模块级常量，存 key、由渲染处 t() 翻译——否则语言切换后拿到的是旧语言。
 */
export const categoryLabelKeys: Record<ServiceCategory, TranslationKey> = {
  time: 'services.category.time',
  'file-share': 'services.category.fileShare',
  'log-monitor': 'services.category.logMonitor',
}

/** 分类图标，总览卡片空态/详情页头部复用 */
export const categoryIcons: Record<ServiceCategory, LucideIcon> = {
  time: FileClock,
  'file-share': FolderOpen,
  'log-monitor': ScrollText,
}

export const reloadModeLabelKeys: Record<ReloadMode, TranslationKey> = {
  hot: 'services.reloadMode.hot',
  restart: 'services.reloadMode.restart',
}
