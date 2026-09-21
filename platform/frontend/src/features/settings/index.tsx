import { Outlet } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { usePermissions } from '@/hooks/use-permissions'
import { LanguageSwitch } from '@/components/language-switch'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { SidebarNav } from './components/sidebar-nav'
import { settingsNavItems } from './data/nav'

/**
 * 设置页布局：左子导航 + 右侧分节内容（子路由）。
 *
 * 设置对所有人可见，只有「系统更新」子项按角色隐藏——隐藏入口不是权限边界，
 * 相关接口在后端仍要求 admin。
 */
export function SettingsLayout() {
  const { t } = useTranslation()
  const { isAdmin } = usePermissions()
  const items = settingsNavItems.filter((item) => !item.adminOnly || isAdmin)

  return (
    <>
      <Header fixed>
        <Search className='me-auto' />
        <LanguageSwitch />
        <ThemeSwitch />
        <ProfileDropdown />
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div>
          <h2 className='text-2xl font-bold tracking-tight'>
            {t('settings.title')}
          </h2>
          <p className='text-muted-foreground'>{t('settings.description')}</p>
        </div>

        <div className='flex flex-col gap-6 md:flex-row md:gap-8'>
          {/* 宽度挂在包裹层：ScrollArea 是块级子元素，宽度写在它内部的 nav 上不生效 */}
          <div className='md:w-44 md:shrink-0'>
            <SidebarNav items={items} />
          </div>
          <div className='min-w-0 flex-1'>
            <Outlet />
          </div>
        </div>
      </Main>
    </>
  )
}
