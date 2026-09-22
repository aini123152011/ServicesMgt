import { getRouteApi } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/layout/header-actions'
import { Main } from '@/components/layout/main'
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
  const { t } = useTranslation()

  return (
    <UsersProvider>
      <Header fixed>
        <HeaderActions />
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>
              {t('users.title')}
            </h2>
            <p className='text-muted-foreground'>{t('users.description')}</p>
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
            <p className='text-muted-foreground'>{t('users.listLoadFailed')}</p>
            <Button variant='outline' onClick={() => usersQuery.refetch()}>
              {t('common.retry')}
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
