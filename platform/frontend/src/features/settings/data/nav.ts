import { CircleUser, Info, RefreshCw, SwatchBook } from 'lucide-react'
import { type SettingsNavItem } from '../components/sidebar-nav'

/**
 * 设置页的子导航。
 *
 * 与侧边栏导航同样存 i18n key：这里是模块级常量，存译文会让语言切换后菜单停在旧语言。
 * 「系统更新」仅管理员可见（页面内按 usePermissions 过滤），后端接口仍强制 admin。
 */
export const settingsNavItems: (SettingsNavItem & { adminOnly?: boolean })[] = [
  {
    href: '/settings/account',
    titleKey: 'settings.nav.account',
    icon: CircleUser,
  },
  {
    href: '/settings/appearance',
    titleKey: 'settings.nav.appearance',
    icon: SwatchBook,
  },
  {
    href: '/settings/updates',
    titleKey: 'settings.nav.updates',
    icon: RefreshCw,
    adminOnly: true,
  },
  {
    href: '/settings/about',
    titleKey: 'settings.nav.about',
    icon: Info,
  },
]
