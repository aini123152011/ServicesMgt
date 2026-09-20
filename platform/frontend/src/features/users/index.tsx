import { getRouteApi } from '@tanstack/react-router'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ConfigDrawer } from '@/components/config-drawer'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { UsersDialogs } from './components/users-dialogs'
import { UsersPrimaryButtons } from './components/users-primary-buttons'
import { UsersProvider } from './components/users-provider'
import { UsersTable } from './components/users-table'
import { useUsersQuery } from './hooks/use-users'

const route = getRouteApi('/_authenticated/users/')

export function Users() {
  const search = route.useSearch()
  const navigate = route.useNavigate()
  const usersQuery = useUsersQuery()

  return (
    <UsersProvider>
      <Header fixed>
        <Search className='me-auto' />
        <ThemeSwitch />
        <ConfigDrawer />
        <ProfileDropdown />
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>用户管理</h2>
            <p className='text-muted-foreground'>
              管理平台用户账号与角色分配。
            </p>
          </div>
          <UsersPrimaryButtons />
        </div>
        {usersQuery.isPending ? (
          // 加载骨架：与表格块高度接近，避免数据到达后跳动
          <div className='flex flex-1 flex-col gap-4'>
            <Skeleton className='h-8 w-80' />
            <Skeleton className='h-96 w-full' />
          </div>
        ) : usersQuery.isError ? (
          <div className='flex flex-col items-center gap-3 py-16'>
            <p className='text-muted-foreground'>用户列表加载失败。</p>
            <Button variant='outline' onClick={() => usersQuery.refetch()}>
              重试
            </Button>
          </div>
        ) : (
          <UsersTable
            data={usersQuery.data}
            search={search}
            navigate={navigate}
          />
        )}
      </Main>

      <UsersDialogs />
    </UsersProvider>
  )
}
