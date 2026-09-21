import { Server, Users } from 'lucide-react'
import { type SidebarData } from '../types'

/**
 * 侧边栏导航配置。文案一律存 i18n key（渲染处经 useNavLabel 翻译），
 * 因为这里是模块级常量：直接存中文会让语言切换后菜单停留在旧语言。
 */
export const sidebarData: SidebarData = {
  navGroups: [
    {
      titleKey: 'nav.groupServices',
      // 其下除「服务列表」外，还会按分类动态追加各服务入口
      dynamicServices: true,
      items: [
        {
          titleKey: 'nav.services',
          url: '/services',
          icon: Server,
        },
      ],
    },
    {
      titleKey: 'nav.groupUsers',
      items: [
        {
          titleKey: 'nav.users',
          url: '/users',
          icon: Users,
          // 仅 admin（含 is_superuser）可见，渲染处按 usePermissions 过滤
          adminOnly: true,
        },
      ],
    },
  ],
}
