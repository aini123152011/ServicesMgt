import { Link } from '@tanstack/react-router'
import {
  ChevronsUpDown,
  Info,
  LogOut,
  RefreshCw,
  SwatchBook,
  UserCog,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/auth-store'
import { type TranslationKey } from '@/lib/i18n'
import useDialogState from '@/hooks/use-dialog-state'
import { usePermissions } from '@/hooks/use-permissions'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { SignOutDialog } from '@/components/sign-out-dialog'

type SettingsEntry = {
  to: string
  titleKey: TranslationKey
  icon: LucideIcon
  adminOnly?: boolean
}

/** 设置类入口：这些不占侧边栏，统一收在左下角的身份菜单里 */
const settingsEntries: SettingsEntry[] = [
  { to: '/settings/account', titleKey: 'settings.nav.account', icon: UserCog },
  {
    to: '/settings/appearance',
    titleKey: 'settings.nav.appearance',
    icon: SwatchBook,
  },
  {
    to: '/settings/updates',
    titleKey: 'settings.nav.updates',
    icon: RefreshCw,
    // 后端更新接口强制 admin，非管理员不渲染该入口
    adminOnly: true,
  },
  { to: '/settings/about', titleKey: 'settings.nav.about', icon: Info },
]

/**
 * 侧边栏底部的身份区：显示当前登录者，并承载设置类入口（账号/外观/系统更新/关于）。
 *
 * 顶栏头像菜单只留身份与退出登录，两个身份菜单不重复同一批入口。
 */
export function NavUser() {
  const { isMobile } = useSidebar()
  const [open, setOpen] = useDialogState()
  const { t } = useTranslation()
  const { isAdmin } = usePermissions()
  // 身份必须显示当前登录用户；未登录或 /users/me 尚未返回时回退到"未登录"，避免出现空白
  const currentUser = useAuthStore((state) => state.auth.user)
  const displayName =
    currentUser?.full_name ||
    currentUser?.email?.split('@')[0] ||
    t('common.notLoggedIn')
  const displayEmail = currentUser?.email || ''
  const initials = displayName.slice(0, 2).toUpperCase()

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton
                size='lg'
                className='data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground'
              >
                <Avatar className='h-8 w-8 rounded-lg'>
                  <AvatarImage alt={displayName} />
                  <AvatarFallback className='rounded-lg'>
                    {initials}
                  </AvatarFallback>
                </Avatar>
                <div className='grid flex-1 text-start text-sm leading-tight'>
                  <span className='truncate font-semibold'>{displayName}</span>
                  <span className='truncate text-xs'>{displayEmail}</span>
                </div>
                <ChevronsUpDown className='ms-auto size-4' />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className='w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg'
              // 触发按钮在侧栏底部：桌面端向上弹（否则会弹到侧栏外并贴屏幕底边），
              // 移动端侧栏是整屏抽屉，向上弹会顶出可视区，仍向下
              side={isMobile ? 'bottom' : 'top'}
              align='start'
              sideOffset={4}
            >
              <DropdownMenuLabel className='p-0 font-normal'>
                <div className='flex items-center gap-2 px-1 py-1.5 text-start text-sm'>
                  <Avatar className='h-8 w-8 rounded-lg'>
                    <AvatarImage alt={displayName} />
                    <AvatarFallback className='rounded-lg'>
                      {initials}
                    </AvatarFallback>
                  </Avatar>
                  <div className='grid flex-1 text-start text-sm leading-tight'>
                    <span className='truncate font-semibold'>
                      {displayName}
                    </span>
                    <span className='truncate text-xs'>{displayEmail}</span>
                  </div>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                {settingsEntries
                  .filter((entry) => !entry.adminOnly || isAdmin)
                  .map((entry) => (
                    <DropdownMenuItem key={entry.to} asChild>
                      <Link to={entry.to}>
                        <entry.icon />
                        {t(entry.titleKey)}
                      </Link>
                    </DropdownMenuItem>
                  ))}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant='destructive'
                onClick={() => setOpen(true)}
              >
                <LogOut />
                {t('common.signOut')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>

      <SignOutDialog open={!!open} onOpenChange={setOpen} />
    </>
  )
}
