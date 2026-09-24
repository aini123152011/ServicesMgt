import { useRef, useState } from 'react'
import { Download, LoaderCircle, RefreshCw, Upload } from 'lucide-react'
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
import { taskStatusKey } from '../data/update-status'
import {
  useApplyAllUpdatesMutation,
  useApplyUpdateMutation,
  useCheckUpdatesMutation,
  useSystemInfoQuery,
  useUpdateStatusQuery,
  useUploadPackageMutation,
} from '../hooks/use-system'

/** 镜像 ID 太长，表格里只显示前 12 位（完整值在后端可查） */
function shortImageId(imageId: string | null): string {
  return imageId ? imageId.replace('sha256:', '').slice(0, 12) : '—'
}

/**
 * 平台与服务的镜像更新面板（原「系统信息」页内容）。
 * 页面外壳（页头与标题）由设置页布局与 ContentSection 提供，这里只管更新本身。
 */
export function UpdatePanel() {
  const { t } = useTranslation()
  const { data, isLoading, refetch } = useSystemInfoQuery()
  const statusQuery = useUpdateStatusQuery(true)
  const checkMutation = useCheckUpdatesMutation()
  const uploadMutation = useUploadPackageMutation()
  const applyMutation = useApplyUpdateMutation()
  const applyAllMutation = useApplyAllUpdatesMutation()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [pendingTarget, setPendingTarget] = useState<string | null>(null)

  const status = statusQuery.data ?? data?.status ?? null
  const running = status?.status === 'running'
  const availableCount = (data?.targets ?? []).filter(
    (target) => target.update_available
  ).length

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
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-center justify-end gap-2'>
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
        {/* 一键更新：串行重建全部有新版本的目标；没有可更新目标时不显示 */}
        {availableCount > 0 && (
          <Button
            onClick={() => applyAllMutation.mutate()}
            disabled={
              running || applyAllMutation.isPending || pendingTarget !== null
            }
          >
            {applyAllMutation.isPending || running ? (
              <LoaderCircle className='animate-spin' />
            ) : (
              <Download />
            )}
            {t('system.applyAll', { count: availableCount })}
          </Button>
        )}
      </div>

      {/* 一键更新的批次进度：共几个、做到第几个、失败停在哪一步 */}
      {status?.batch_total ? (
        <div className='flex flex-col gap-1 text-sm'>
          <div className='flex flex-wrap items-center gap-2'>
            <Badge
              variant={status.status === 'failed' ? 'destructive' : 'secondary'}
            >
              {t('system.batchProgress', {
                index: status.batch_index ?? 0,
                total: status.batch_total,
              })}
            </Badge>
            {status.updated_targets && status.updated_targets.length > 0 && (
              <span className='text-muted-foreground'>
                {t('system.batchUpdated', {
                  targets: status.updated_targets.join('、'),
                })}
              </span>
            )}
          </div>
          {status.failed_targets && status.failed_targets.length > 0 && (
            <span className='text-destructive'>
              {t('system.batchFailed', {
                targets: status.failed_targets.join('、'),
              })}
            </span>
          )}
        </div>
      ) : null}

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
                    <tr key={target.target} className='border-b last:border-0'>
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
    </div>
  )
}
