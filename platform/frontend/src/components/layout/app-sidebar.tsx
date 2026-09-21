import { useLayout } from '@/context/layout-provider'
import { usePermissions } from '@/hooks/use-permissions'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
} from '@/components/ui/sidebar'
import { buildServiceNavItems } from '@/features/services/data/nav'
import { useServicesQuery } from '@/features/services/hooks/use-services'
import { AppTitle } from './app-title'
import { sidebarData } from './data/sidebar-data'
import { NavGroup } from './nav-group'
import { NavUser } from './nav-user'

export function AppSidebar() {
  const { collapsible, variant } = useLayout()
  const { isAdmin } = usePermissions()
  // 服务入口按分类挂到「服务管理」下；列表未就绪时该组只有「服务总览」，不阻塞渲染
  const { data: services } = useServicesQuery()
  const serviceItems = buildServiceNavItems(services ?? [])

  const navGroups = sidebarData.navGroups.map((group) => ({
    ...group,
    // adminOnly 项按角色过滤，其余导航所有人可见
    items: [
      ...group.items,
      ...(group.dynamicServices ? serviceItems : []),
    ].filter((item) => !item.adminOnly || isAdmin),
  }))

  return (
    <Sidebar collapsible={collapsible} variant={variant}>
      <SidebarHeader>
        <AppTitle />
      </SidebarHeader>
      <SidebarContent>
        {navGroups.map((props, index) => (
          // 分组标题可能是 key 或服务名原文；无标题的分组（首页、设置）退回下标，
          // 否则 key 为 undefined，React 会按顺序复用节点而报重复 key 警告
          <NavGroup key={props.titleKey ?? props.title ?? index} {...props} />
        ))}
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
