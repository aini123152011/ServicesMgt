import { Server, Users } from 'lucide-react'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  navGroups: [
    {
      title: '服务管理',
      items: [
        {
          title: '服务列表',
          url: '/services',
          icon: Server,
        },
      ],
    },
    {
      title: '用户管理',
      items: [
        {
          title: '用户列表',
          url: '/users',
          icon: Users,
          // 仅 admin（含 is_superuser）可见，渲染处按 usePermissions 过滤
          adminOnly: true,
        },
      ],
    },
  ],
}
