import { type LinkProps } from '@tanstack/react-router'
import { type TranslationKey } from '@/lib/i18n'

type BaseNavItem = {
  /** 平台自有导航文案的 i18n key；渲染处经 useNavLabel 翻译（模块级常量不能调 t） */
  titleKey?: TranslationKey
  /** 直接展示的文案：服务元数据本轮保持中文，不走 i18n */
  title?: string
  badge?: string
  icon?: React.ElementType
  /** true 时仅 admin（含 is_superuser）可见，渲染处按 usePermissions 过滤 */
  adminOnly?: boolean
}

type NavLink = BaseNavItem & {
  url: LinkProps['to'] | (string & {})
  items?: never
}

type NavCollapsible = BaseNavItem & {
  items: (BaseNavItem & { url: LinkProps['to'] | (string & {}) })[]
  url?: never
}

type NavItem = NavCollapsible | NavLink

type NavGroup = BaseNavItem & {
  items: NavItem[]
  /** true 时渲染处会追加「按服务分类」的动态菜单项（见 features/services/data/nav.ts） */
  dynamicServices?: boolean
}

type SidebarData = {
  navGroups: NavGroup[]
}

export type { SidebarData, NavGroup, NavItem, NavCollapsible, NavLink }
