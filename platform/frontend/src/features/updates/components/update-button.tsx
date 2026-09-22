import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowUpCircle, Check, LoaderCircle, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { type UpdateTarget } from '@/api/system'
import { usePermissions } from '@/hooks/use-permissions'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { taskStatusKey } from '@/features/settings/data/update-status'
import {
  useApplyUpdateMutation,
  useSystemInfoQuery,
  useUpdateStatusQuery,
} from '@/features/settings/hooks/use-system'

/** 自动检查间隔：接口只做本地镜像比对（不 pull），5 分钟一次代价很低 */
const AUTO_CHECK_INTERVAL_MS = 5 * 60 * 1000

/** 镜像 ID 太长，只显示前 12 位（完整值在「设置 → 系统更新」可查） */
function shortImageId(imageId: string | null): string {
  return imageId ? imageId.replace('sha256:', '').slice(0, 12) : '—'
}

/**
 * 顶栏的更新入口（仅管理员）：自动检测可用更新，并在原地提供逐个目标的更新按钮。
 *
 * 「自动检测」= 定时拉 `/system/info`（后端比对容器镜像与同名 tag 当前指向的镜像 ID，
 * 不访问镜像仓库），有可用更新时按钮上出现圆点与数量。真正的在线更新走
 * `/system/updates/apply`，平台自身由 helper 容器重建，服务容器后台重建。
 * 离线包上传等完整能力仍在「设置 → 系统更新」。
 */
export function UpdateButton() {
  const { t } = useTranslation()
  const { isAdmin } = usePermissions()
  const [open, setOpen] = useState(false)
  const [pendingTarget, setPendingTarget] = useState<string | null>(null)
  const infoQuery = useSystemInfoQuery(AUTO_CHECK_INTERVAL_MS)
  const statusQuery = useUpdateStatusQuery(true)
  const applyMutation = useApplyUpdateMutation()

  const targets = infoQuery.data?.targets ?? []
  const available = targets.filter((target) => target.update_available)
  const status = statusQuery.data ?? infoQuery.data?.status ?? null
  const running = status?.status === 'running'
  const failed = status?.status === 'failed'

  // 权限在组件内自守，不依赖调用方：更新接口后端强制 admin，非管理员渲染出来点下去只会 403
  if (!isAdmin) {
    return null
  }

  // 没有可用更新、也没有在跑或失败的更新任务时整块不渲染：常驻一个点开只说「已是最新」
  // 的入口是噪音。任务在跑或失败必须留口，否则用户看不到正在进行的更新与失败原因；
  // 手动检查更新与离线包上传仍在「设置 → 系统更新」，能力没有减少。
  // 早退放在 hooks 之后：5 分钟自动检测照常进行，只是不渲染入口。
  if (available.length === 0 && !running && !failed) {
    return null
  }

  const handleApply = (target: UpdateTarget) => {
    setPendingTarget(target.target)
    applyMutation.mutate(
      { target: target.target, image: target.image },
      {
        onSuccess: () => toast.success(t('system.quick.applied')),
        onSettled: () => setPendingTarget(null),
      }
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size='icon'
          variant='ghost'
          className='relative rounded-full'
          aria-label={
            available.length > 0
              ? t('system.quick.availableCount', { count: available.length })
              : t('system.quick.tooltip')
          }
        >
          {running || infoQuery.isFetching ? (
            <LoaderCircle className='animate-spin' aria-hidden='true' />
          ) : (
            <ArrowUpCircle aria-hidden='true' />
          )}
          {/* 有可用更新时点一个红点：图标本身保持中性，避免整块顶栏花掉 */}
          {available.length > 0 && (
            <span
              className='absolute end-1 top-1 size-2 rounded-full bg-destructive'
              aria-hidden='true'
            />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align='end' className='w-80 p-0'>
        <div className='flex items-center justify-between gap-2 px-4 py-3'>
          <div>
            <p className='text-sm font-medium'>{t('system.quick.title')}</p>
            <p className='text-xs text-muted-foreground'>
              {t('system.version.current')}: {infoQuery.data?.version ?? '—'}
            </p>
          </div>
          <Button
            size='sm'
            variant='outline'
            disabled={running}
            onClick={() => infoQuery.refetch()}
          >
            <RefreshCw />
            {t('system.check')}
          </Button>
        </div>
        <Separator />

        <div className='max-h-72 overflow-y-auto'>
          {targets.length === 0 ? (
            <p className='px-4 py-6 text-center text-sm text-muted-foreground'>
              {infoQuery.data?.docker_available === false
                ? t('system.dockerUnavailable')
                : t('system.table.empty')}
            </p>
          ) : available.length === 0 ? (
            <p className='flex items-center justify-center gap-2 px-4 py-6 text-sm text-muted-foreground'>
              <Check className='size-4' />
              {t('system.quick.upToDate')}
            </p>
          ) : (
            <ul className='divide-y'>
              {available.map((target) => (
                <li
                  key={target.target}
                  className='flex items-center justify-between gap-3 px-4 py-3'
                >
                  <div className='min-w-0'>
                    <p className='truncate text-sm'>{target.display_name}</p>
                    <p className='truncate font-mono text-xs text-muted-foreground'>
                      {shortImageId(target.running_image_id)} →{' '}
                      {shortImageId(target.available_image_id)}
                    </p>
                  </div>
                  <Button
                    size='sm'
                    disabled={running}
                    onClick={() => handleApply(target)}
                  >
                    {pendingTarget === target.target && (
                      <LoaderCircle className='animate-spin' />
                    )}
                    {t('system.table.apply')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <Separator />
        <div className='flex items-center justify-between gap-2 px-4 py-3'>
          <span className='text-xs text-muted-foreground'>
            {running && status
              ? `${t(taskStatusKey(status.status))}${
                  status.target ? ` · ${status.target}` : ''
                }`
              : t('system.quick.autoCheckHint')}
          </span>
          {failed ? (
            <Badge variant='destructive'>{t('system.status.failed')}</Badge>
          ) : null}
          <Button size='sm' variant='ghost' asChild>
            <Link to='/settings/updates' onClick={() => setOpen(false)}>
              {t('system.quick.more')}
            </Link>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
