import { useState } from 'react'
import { Eye, History, LoaderCircle, RotateCcw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { type ServiceConfigVersion, type ServiceField } from '@/api/services'
import { usePermissions } from '@/hooks/use-permissions'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ConfirmDialog } from '@/components/confirm-dialog'
import {
  useConfigVersionDetailQuery,
  useConfigVersionsQuery,
  useRollbackConfigMutation,
} from '../hooks/use-services'

type ServiceConfigHistoryProps = {
  name: string
  /** 当前 schema 的字段定义，用于把 values 的键翻成可读标签 */
  fields: ServiceField[]
}

/** 缺失字段的占位符，避免单元格出现空白让人以为渲染出错 */
const EMPTY_PLACEHOLDER = '—'

/**
 * 配置历史卡片：列出该服务的配置版本，可查看内容与一键回滚。
 *
 * 回滚走的是后端「渲染 → 生效 → 记新版本」的同一条链路，因此这里只需给出确认与提示；
 * 回滚结果由 useRollbackConfigMutation 统一 toast，卡片本身不重复提示。
 */
export function ServiceConfigHistory({
  name,
  fields,
}: ServiceConfigHistoryProps) {
  const { t } = useTranslation()
  const { isOperator } = usePermissions()
  const versionsQuery = useConfigVersionsQuery(name)
  const rollbackMutation = useRollbackConfigMutation(name)
  // 待回滚的版本：非空时弹确认框
  const [pendingRollback, setPendingRollback] =
    useState<ServiceConfigVersion | null>(null)
  // 正在查看内容的版本号：非空时弹详情框
  const [viewingVersion, setViewingVersion] = useState<number | null>(null)
  const detailQuery = useConfigVersionDetailQuery(name, viewingVersion)

  const versions = versionsQuery.data ?? []

  return (
    <Card className='w-full'>
      <CardHeader>
        <div className='flex flex-wrap items-center justify-between gap-4'>
          <div>
            <CardTitle className='flex items-center gap-2'>
              <History className='size-4' />
              {t('services.history.title')}
            </CardTitle>
            <CardDescription>
              {t('services.history.description')}
            </CardDescription>
          </div>
          {versionsQuery.isFetching && (
            <LoaderCircle className='size-4 animate-spin text-muted-foreground' />
          )}
        </div>
      </CardHeader>
      <CardContent>
        {versionsQuery.isPending ? (
          <div className='flex flex-col gap-2'>
            <Skeleton className='h-8 w-full' />
            <Skeleton className='h-8 w-full' />
          </div>
        ) : versionsQuery.isError ? (
          <div className='flex flex-col items-center gap-3 py-8'>
            <p className='text-muted-foreground'>
              {t('services.history.loadFailed')}
            </p>
            <Button variant='outline' onClick={() => versionsQuery.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : versions.length === 0 ? (
          <p className='py-6 text-center text-sm text-muted-foreground'>
            {t('services.history.empty')}
          </p>
        ) : (
          <div className='overflow-x-auto'>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className='w-20'>
                    {t('services.history.column.version')}
                  </TableHead>
                  <TableHead className='w-48'>
                    {t('services.history.column.time')}
                  </TableHead>
                  <TableHead className='w-56'>
                    {t('services.history.column.user')}
                  </TableHead>
                  <TableHead className='w-24'>
                    {t('services.history.column.state')}
                  </TableHead>
                  <TableHead className='w-32'>
                    {t('services.history.column.digest')}
                  </TableHead>
                  <TableHead className='w-40 text-right'>
                    {t('services.history.column.actions')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className='font-medium'>
                      v{item.version}
                      {item.rolled_back_from !== null && (
                        <Badge variant='outline' className='ms-2'>
                          {t('services.history.rollbackFrom', {
                            version: item.rolled_back_from,
                          })}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className='text-muted-foreground'>
                      {formatTime(item.created_at)}
                    </TableCell>
                    <TableCell className='text-muted-foreground'>
                      {item.user_email ?? EMPTY_PLACEHOLDER}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={item.applied ? 'secondary' : 'outline'}
                        className={
                          item.applied
                            ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                            : undefined
                        }
                        // 这是「下发那一刻」的快照：容器当时没运行就是未生效，
                        // 之后启动容器也不会回填这一列
                        title={t('services.history.stateHint')}
                      >
                        {item.applied
                          ? t('services.history.applied')
                          : t('services.history.notApplied')}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <code className='text-xs text-muted-foreground'>
                        {item.rendered_digest || EMPTY_PLACEHOLDER}
                      </code>
                    </TableCell>
                    <TableCell className='text-right'>
                      <Button
                        variant='ghost'
                        size='sm'
                        // 每行按钮文字相同，读屏与测试都需要带上版本号才分得清是哪一行
                        aria-label={t('services.history.viewVersion', {
                          version: item.version,
                        })}
                        onClick={() => setViewingVersion(item.version)}
                      >
                        <Eye />
                        {t('services.history.view')}
                      </Button>
                      <Button
                        variant='ghost'
                        size='sm'
                        aria-label={t('services.history.rollbackVersion', {
                          version: item.version,
                        })}
                        disabled={!isOperator || rollbackMutation.isPending}
                        title={
                          isOperator
                            ? undefined
                            : t('services.detail.readonlyAction')
                        }
                        onClick={() => setPendingRollback(item)}
                      >
                        {rollbackMutation.isPending &&
                        rollbackMutation.variables === item.version ? (
                          <LoaderCircle className='animate-spin' />
                        ) : (
                          <RotateCcw />
                        )}
                        {t('services.history.rollback')}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      {/* 查看版本内容：values 已由后端脱敏，这里只做键到标签的翻译 */}
      <Dialog
        open={viewingVersion !== null}
        onOpenChange={(open) => !open && setViewingVersion(null)}
      >
        <DialogContent className='max-h-[80vh] overflow-y-auto sm:max-w-2xl'>
          <DialogHeader>
            <DialogTitle>
              {t('services.history.detailTitle', { version: viewingVersion })}
            </DialogTitle>
            <DialogDescription>
              {detailQuery.data
                ? t('services.history.detailMeta', {
                    time: formatTime(detailQuery.data.created_at),
                    user: detailQuery.data.user_email ?? t('common.unknown'),
                    digest:
                      detailQuery.data.rendered_digest || EMPTY_PLACEHOLDER,
                  })
                : t('common.loading')}
            </DialogDescription>
          </DialogHeader>
          {detailQuery.isPending ? (
            <Skeleton className='h-40 w-full' />
          ) : detailQuery.isError ? (
            <p className='py-6 text-center text-sm text-muted-foreground'>
              {t('services.history.detailFailed')}
            </p>
          ) : Object.keys(detailQuery.data?.values ?? {}).length === 0 ? (
            <p className='py-6 text-center text-sm text-muted-foreground'>
              {t('services.history.detailEmpty')}
            </p>
          ) : (
            <dl className='grid gap-2'>
              {orderedEntries(detailQuery.data?.values ?? {}, fields).map(
                ([key, value]) => (
                  <div
                    key={key}
                    className='grid grid-cols-[minmax(0,14rem)_1fr] gap-3 border-b pb-2 last:border-b-0'
                  >
                    <dt className='text-sm text-muted-foreground'>
                      {fieldLabel(key, fields)}
                      <code className='ms-1 text-xs'>{key}</code>
                    </dt>
                    <dd className='text-sm break-all'>{formatValue(value)}</dd>
                  </div>
                )
              )}
            </dl>
          )}
        </DialogContent>
      </Dialog>

      {/* 回滚确认：回滚是覆盖当前生效配置的动作，必须先说清会新增一个版本 */}
      <ConfirmDialog
        open={pendingRollback !== null}
        onOpenChange={(open) => !open && setPendingRollback(null)}
        title={t('services.history.rollbackTitle', {
          version: pendingRollback?.version,
        })}
        desc={t('services.history.rollbackDesc')}
        confirmText={t('services.history.rollbackConfirm')}
        isLoading={rollbackMutation.isPending}
        handleConfirm={() => {
          if (pendingRollback) {
            rollbackMutation.mutate(pendingRollback.version)
          }
          setPendingRollback(null)
        }}
      />
    </Card>
  )
}

/** 后端给的是 UTC ISO 串，按浏览器本地时区展示；解析失败时原样显示，不显示 Invalid Date */
function formatTime(value: string | null): string {
  if (!value) return EMPTY_PLACEHOLDER
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/** 字段标签：schema 里有就用可读标签，没有（schema 已删该字段）就回退到键名 */
function fieldLabel(key: string, fields: ServiceField[]): string {
  return fields.find((field) => field.name === key)?.label ?? key
}

/** 值渲染：数组按「、」拼接，对象走 JSON，布尔与空值给出可读文案 */
function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return EMPTY_PLACEHOLDER
  }
  if (Array.isArray(value)) return value.map(String).join('、')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/**
 * 按 schema 顺序排列历史值，schema 里已不存在的键排在末尾。
 *
 * 直接 Object.entries 会按 JSON 里的插入顺序展示，跨版本顺序不一致，人眼比对时容易漏项。
 */
function orderedEntries(
  values: Record<string, unknown>,
  fields: ServiceField[]
): [string, unknown][] {
  const known = fields
    .map((field) => field.name)
    .filter((fieldName) => fieldName in values)
  const rest = Object.keys(values).filter((key) => !known.includes(key))
  return [...known, ...rest].map((key) => [key, values[key]])
}
