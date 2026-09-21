import { useRef, useState } from 'react'
import { LoaderCircle, RefreshCw, Upload } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { type UpdateTarget } from '@/api/system'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { LanguageSwitch } from '@/components/language-switch'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import {
  useApplyUpdateMutation,
  useCheckUpdatesMutation,
  useSystemInfoQuery,
  useUpdateStatusQuery,
  useUploadPackageMutation,
} from './hooks/use-system'

/** 后端可能返回的任务状态；未知值一律按 idle 显示，避免把 i18n key 露到界面上 */
const KNOWN_TASK_STATUSES = ['idle', 'running', 'succeeded', 'failed'] as const

function taskStatusKey(status: string | null | undefined): string {
  return KNOWN_TASK_STATUSES.includes(
    status as (typeof KNOWN_TASK_STATUSES)[number]
  )
    ? `system.status.${status}`
    : 'system.status.idle'
}

/** 镜像 ID 太长，表格里只显示前 12 位（完整值在后端可查） */
function shortImageId(imageId: string | null): string {
  return imageId ? imageId.replace('sha256:', '').slice(0, 12) : '—'
}

export function System() {
  const { t } = useTranslation()
  const { data, isLoading, refetch } = useSystemInfoQuery()
  const statusQuery = useUpdateStatusQuery(true)
  const checkMutation = useCheckUpdatesMutation()
  const uploadMutation = useUploadPackageMutation()
  const applyMutation = useApplyUpdateMutation()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [pendingTarget, setPendingTarget] = useState<string | null>(null)

  const status = statusQuery.data ?? data?.status ?? null
  const running = status?.status === 'running'

  const handleUpload = () => {
    const file = fileInputRef.current?.files?.[0]
    if (!file) return
    uploadMutation.mutate(file, {
      onSuccess: () => {
        if (fileInputRef.current) fileInputRef.current.value = ''
      },
    })
  }

  const handleApply = (target: UpdateTarget) => {
    setPendingTarget(target.target)
    applyMutation.mutate(
      { target: target.target, image: target.image },
      { onSettled: () => setPendingTarget(null) }
    )
  }

  return (
    <>
      <Header fixed>
        <Search />
        <div className='ms-auto flex items-center gap-2'>
          <LanguageSwitch />
          <ThemeSwitch />
          <ProfileDropdown />
        </div>
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>
              {t('system.title')}
            </h2>
            <p className='text-muted-foreground'>{t('system.subtitle')}</p>
          </div>
          <Button
            variant='outline'
            onClick={() => checkMutation.mutate()}
            disabled={checkMutation.isPending || running}
          >
            {checkMutation.isPending ? (
              <LoaderCircle className='animate-spin' />
            ) : (
              <RefreshCw />
            )}
            {t('system.check')}
          </Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>{t('system.version.title')}</CardTitle>
            <CardDescription>{t('system.version.description')}</CardDescription>
          </CardHeader>
          <CardContent className='grid gap-2 text-sm sm:grid-cols-2'>
            <div>
              <span className='text-muted-foreground'>
                {t('system.version.current')}:{' '}
              </span>
              {isLoading ? (
                <Skeleton className='inline-block h-4 w-16' />
              ) : (
                data?.version
              )}
            </div>
            <div>
              <span className='text-muted-foreground'>
                {t('system.version.build')}:{' '}
              </span>
              {data?.build || '—'}
            </div>
            <div>
              <span className='text-muted-foreground'>
                {t('system.version.registry')}:{' '}
              </span>
              {data?.update_registry || t('system.version.registryUnset')}
            </div>
            <div>
              <span className='text-muted-foreground'>
                {t('system.version.task')}:{' '}
              </span>
              {status ? (
                <Badge
                  variant={
                    status.status === 'failed' ? 'destructive' : 'secondary'
                  }
                >
                  {t(taskStatusKey(status.status))}
                </Badge>
              ) : (
                t('system.status.idle')
              )}
            </div>
            {status?.message && (
              <p className='text-muted-foreground sm:col-span-2'>
                {status.message}
              </p>
            )}
            {data && !data.docker_available && (
              <p className='text-destructive sm:col-span-2'>
                {t('system.dockerUnavailable')}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t('system.targets.title')}</CardTitle>
            <CardDescription>{t('system.targets.description')}</CardDescription>
          </CardHeader>
          <CardContent className='space-y-3'>
            <div className='flex flex-wrap items-center gap-2'>
              <Input
                ref={fileInputRef}
                type='file'
                accept='.tar,.tar.gz,.tgz'
                className='max-w-md'
                aria-label={t('system.package.label')}
              />
              <Button
                variant='outline'
                onClick={handleUpload}
                disabled={uploadMutation.isPending || running}
              >
                {uploadMutation.isPending ? (
                  <LoaderCircle className='animate-spin' />
                ) : (
                  <Upload />
                )}
                {t('system.package.upload')}
              </Button>
            </div>
            <p className='text-sm text-muted-foreground'>
              {t('system.package.hint')}
            </p>

            {isLoading ? (
              <Skeleton className='h-24 w-full' />
            ) : (
              <div className='overflow-x-auto'>
                <table className='w-full text-sm'>
                  <thead>
                    <tr className='border-b text-left text-muted-foreground'>
                      <th className='py-2 pe-3 font-medium'>
                        {t('system.table.target')}
                      </th>
                      <th className='py-2 pe-3 font-medium'>
                        {t('system.table.image')}
                      </th>
                      <th className='py-2 pe-3 font-medium'>
                        {t('system.table.running')}
                      </th>
                      <th className='py-2 pe-3 font-medium'>
                        {t('system.table.available')}
                      </th>
                      <th className='py-2 pe-3 font-medium'>
                        {t('system.table.state')}
                      </th>
                      <th className='py-2 font-medium'>
                        {t('system.table.actions')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data?.targets ?? []).map((target) => (
                      <tr
                        key={target.target}
                        className='border-b last:border-0'
                      >
                        <td className='py-2 pe-3'>
                          {target.display_name}
                          <span className='ms-1 text-xs text-muted-foreground'>
                            ({target.target})
                          </span>
                        </td>
                        <td className='py-2 pe-3 font-mono text-xs'>
                          {target.image}
                        </td>
                        <td className='py-2 pe-3 font-mono text-xs'>
                          {shortImageId(target.running_image_id)}
                        </td>
                        <td className='py-2 pe-3 font-mono text-xs'>
                          {shortImageId(target.available_image_id)}
                        </td>
                        <td className='py-2 pe-3'>
                          {target.update_available ? (
                            <Badge>{t('system.table.updateAvailable')}</Badge>
                          ) : (
                            <Badge variant='secondary'>
                              {t('system.table.upToDate')}
                            </Badge>
                          )}
                        </td>
                        <td className='py-2'>
                          <Button
                            size='sm'
                            variant='outline'
                            disabled={running || !target.update_available}
                            onClick={() => handleApply(target)}
                          >
                            {pendingTarget === target.target && (
                              <LoaderCircle className='animate-spin' />
                            )}
                            {t('system.table.apply')}
                          </Button>
                        </td>
                      </tr>
                    ))}
                    {(data?.targets ?? []).length === 0 && (
                      <tr>
                        <td
                          colSpan={6}
                          className='py-6 text-center text-muted-foreground'
                        >
                          {t('system.table.empty')}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}

            <div className='flex items-center gap-2'>
              <Button variant='ghost' size='sm' onClick={() => refetch()}>
                <RefreshCw />
                {t('common.refresh')}
              </Button>
              {running && (
                <span className='text-sm text-muted-foreground'>
                  {t('system.runningHint')}
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      </Main>
    </>
  )
}
