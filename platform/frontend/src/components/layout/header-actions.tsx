import { usePermissions } from '@/hooks/use-permissions'
import { LanguageSwitch } from '@/components/language-switch'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { UpdateButton } from '@/features/updates/components/update-button'

/**
 * 页头右侧的公共操作区：搜索、更新（仅 admin）、语言、主题与身份菜单。
 *
 * 各页原先各自拼一遍这组控件，加一个入口就要改七处；统一到这里，
 * 页头只需要 `<Header fixed><HeaderActions /></Header>`。
 */
export function HeaderActions() {
  const { isAdmin } = usePermissions()
  return (
    <>
      <Search className='me-auto' />
      {/* 更新需要 admin 权限（后端强制），非管理员不渲染。
          放在右侧一组的最左边：它是有红点时最需要被看见的那个，靠头像太近容易被忽略 */}
      {isAdmin && <UpdateButton />}
      <LanguageSwitch />
      <ThemeSwitch />
      <ProfileDropdown />
    </>
  )
}
