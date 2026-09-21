import { Link, useLocation, useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { type TranslationKey } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { useNavLabel } from '@/hooks/use-nav-label'
import { buttonVariants } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

/** 设置页各分区的路径；写成字面量联合，Link 与 navigate 都能做编译期校验 */
export type SettingsNavPath =
  | '/settings/account'
  | '/settings/appearance'
  | '/settings/updates'
  | '/settings/about'

export type SettingsNavItem = {
  href: SettingsNavPath
  /** 平台自有文案，存 key 由渲染处翻译（语言切换后菜单要跟着变） */
  titleKey: TranslationKey
  icon: React.ElementType
}

type SidebarNavProps = React.HTMLAttributes<HTMLElement> & {
  items: SettingsNavItem[]
}

/**
 * 设置页的左侧子导航，窄屏降级为下拉选择。
 *
 * 选中项直接取自当前 pathname，不另存 state：路由若由侧边栏或链接跳转而来，
 * 本地 state 不会跟着变，会出现「地址已是 /settings/about、导航仍高亮旧项」。
 */
export function SidebarNav({ className, items, ...props }: SidebarNavProps) {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const navLabel = useNavLabel()

  return (
    <>
      <div className='md:hidden'>
        <Select
          value={pathname}
          onValueChange={(href) => {
            // Select 回传的是 string，先按导航项收窄再跳转，避免用断言把脏值塞给 router
            const target = items.find((item) => item.href === href)
            if (target) void navigate({ to: target.href })
          }}
        >
          <SelectTrigger
            className='h-12 w-full'
            aria-label={t('settings.nav.label')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {items.map((item) => (
              <SelectItem key={item.href} value={item.href}>
                <div className='flex items-center gap-2'>
                  <item.icon className='size-4' />
                  <span>{navLabel(item)}</span>
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <ScrollArea
        orientation='vertical'
        className='hidden max-h-[calc(100svh-16rem)] md:block'
      >
        <nav className={cn('flex flex-col gap-1 pe-1', className)} {...props}>
          {items.map((item) => (
            <Link
              key={item.href}
              to={item.href}
              className={cn(
                buttonVariants({ variant: 'ghost' }),
                pathname === item.href
                  ? 'bg-muted hover:bg-accent'
                  : 'hover:bg-accent hover:underline',
                'justify-start'
              )}
            >
              <item.icon className='me-2 size-4' />
              {navLabel(item)}
            </Link>
          ))}
        </nav>
      </ScrollArea>
    </>
  )
}
