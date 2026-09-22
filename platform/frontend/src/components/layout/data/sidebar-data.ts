import { LayoutDashboard, ScrollText, Server, Users } from 'lucide-react'
import { type SidebarData } from '../types'

/**
 * 侧边栏导航配置。文案一律存 i18n key（渲染处经 useNavLabel 翻译），
 * 因为这里是模块级常量：直接存中文会让语言切换后菜单停留在旧语言。
 */
export const sidebarData: SidebarData = {
  navGroups: [
    {
      // 「总览」= 看状态与看目录：首页给状态一眼可见，服务列表给全量目录（端口/生效方式）
      titleKey: 'nav.groupOverview',
      items: [
        {
          titleKey: 'nav.home',
          url: '/',
          icon: LayoutDashboard,
        },
        {
          titleKey: 'nav.services',
          url: '/services',
          icon: Server,
        },
      ],
    },
    {
      // 本组只放按分类的服务入口（时间同步/文件共享/日志监控），由服务列表动态生成；
      // 服务列表本身已上移到「总览」，此处不再重复
      titleKey: 'nav.groupServices',
      dynamicServices: true,
      items: [],
    },
    {
      // 管理类入口（用户与审计）都限 admin，故合并为一组；非管理员整组不渲染
      titleKey: 'nav.groupSystem',
      items: [
        {
          titleKey: 'nav.users',
          url: '/users',
          icon: Users,
          // 仅 admin（含 is_superuser）可见，渲染处按 usePermissions 过滤
          adminOnly: true,
        },
        {
          titleKey: 'nav.audit',
          url: '/audit',
          icon: ScrollText,
          adminOnly: true,
        },
      ],
    },
  ],
}
