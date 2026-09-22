import { useRef } from 'react'
import { getRouteApi } from '@tanstack/react-router'
import { RotateCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/layout/header-actions'
import { Main } from '@/components/layout/main'
import { useServicesQuery } from '@/features/services/hooks/use-services'
import {
  actionKind,
  actionKindClassNames,
  actionLabelKey,
} from './data/actions'
import { useAuditActionsQuery, useAuditLogsPageQuery } from './hooks/use-audit'

const route = getRouteApi('/_authenticated/audit/')

/** 缺失字段的占位符，避免单元格出现空白让人以为渲染出错 */
const EMPTY_PLACEHOLDER = '—'

/** 「不筛选」在下拉里的取值：Radix Select 不接受空串，用这个哨兵值代替 */
const ALL = '__all__'

/** 每页条数选项，与后端 limit 上限（500）留出余量 */
const PAGE_SIZE_OPTIONS = [20, 50, 100]

/**
 * 审计日志页（仅管理员可见）。
 *
 * 分页与过滤都走服务端：审计表只增不减，本地过滤只能看到当前页，页数与命中数会失真。
 * 查询条件同步在 URL 上，刷新和分享链接后仍是同一批结果。
 */
export function AuditLogs() {
  const { t } = useTranslation()
  const search = route.useSearch()
  const navigate = route.useNavigate()

  const page = search.page ?? 1
  const pageSize = search.pageSize ?? PAGE_SIZE_OPTIONS[0]
  const action = search.action ?? ''
  const service = search.service ?? ''
  const keyword = search.q ?? ''

  // 关键字输入是非受控的：逐字符进 URL 会让每敲一下都发一次请求。URL 上的关键字变化
  // （前进后退、外链）靠 key 重挂载回填。
  const keywordInput = useRef<HTMLInputElement>(null)

  const logsQuery = useAuditLogsPageQuery({
    offset: (page - 1) * pageSize,
    limit: pageSize,
    action,
    serviceName: service,
    q: keyword,
  })
  const actionsQuery = useAuditActionsQuery()
  const servicesQuery = useServicesQuery()

  const entries = logsQuery.data?.data ?? []
  // count 是「当前过滤条件下的总数」，不是本页条数：分页器据此算总页数
  const total = logsQuery.data?.count ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const updateSearch = (
    patch: Partial<{
      page: number
      pageSize: number
      action: string
      service: string
      q: string
    }>
  ) => {
    // 任何条件变化都回到第一页，否则会停在一个过滤后不存在的页码上
    navigate({ search: (prev) => ({ ...prev, page: 1, ...patch }) })
  }

  // 回车与点「搜索」走同一条路径。不依赖表单的隐式提交：实测在内置浏览器（webview）里
  // 回车只到 keydown、不触发 submit，用户敲完回车看不到任何反应。
  const submitKeyword = () => {
    updateSearch({ q: keywordInput.current?.value.trim() ?? '' })
  }

  return (
    <>
      <Header fixed>
        <HeaderActions />
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>
              {t('audit.title')}
            </h2>
            <p className='text-muted-foreground'>{t('audit.description')}</p>
          </div>
          <Button
            variant='outline'
            onClick={() => logsQuery.refetch()}
            disabled={logsQuery.isFetching}
          >
            <RotateCw />
            {t('common.refresh')}
          </Button>
        </div>

        <div className='flex flex-wrap items-center gap-2'>
          <Select
            value={action || ALL}
            onValueChange={(value) =>
              updateSearch({ action: value === ALL ? '' : value })
            }
          >
            <SelectTrigger
              className='w-52'
              aria-label={t('audit.filter.action')}
            >
              <SelectValue placeholder={t('audit.filter.action')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>
                {t('audit.filter.allActions')}
              </SelectItem>
              {(actionsQuery.data ?? []).map((name) => (
                <SelectItem key={name} value={name}>
                  {actionLabelKey(name) ? t(actionLabelKey(name)!) : name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={service || ALL}
            onValueChange={(value) =>
              updateSearch({ service: value === ALL ? '' : value })
            }
          >
            <SelectTrigger
              className='w-52'
              aria-label={t('audit.filter.service')}
            >
              <SelectValue placeholder={t('audit.filter.service')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>
                {t('audit.filter.allServices')}
              </SelectItem>
              {(servicesQuery.data ?? []).map((item) => (
                <SelectItem key={item.name} value={item.name}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <form
            className='flex items-center gap-2'
            onSubmit={(event) => {
              event.preventDefault()
              submitKeyword()
            }}
          >
            <Input
              key={keyword}
              ref={keywordInput}
              className='w-64'
              defaultValue={keyword}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  submitKeyword()
                }
              }}
              placeholder={t('audit.filter.keywordPlaceholder')}
              aria-label={t('audit.filter.keyword')}
            />
            <Button type='submit' variant='secondary'>
              {t('common.search')}
            </Button>
          </form>
        </div>

        {logsQuery.isPending ? (
          <Skeleton className='h-96 w-full' />
        ) : logsQuery.isError ? (
          <div className='flex flex-col items-center gap-3 py-16'>
            <p className='text-muted-foreground'>{t('audit.loadFailed')}</p>
            <Button variant='outline' onClick={() => logsQuery.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : (
          <>
            <div className='overflow-hidden rounded-md border'>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className='w-56'>
                      {t('audit.field.time')}
                    </TableHead>
                    <TableHead className='w-56'>
                      {t('audit.field.actor')}
                    </TableHead>
                    <TableHead className='w-40'>
                      {t('audit.field.action')}
                    </TableHead>
                    <TableHead className='w-32'>
                      {t('audit.field.service')}
                    </TableHead>
                    <TableHead>{t('audit.field.detail')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.length ? (
                    entries.map((entry) => (
                      <TableRow key={entry.id}>
                        <TableCell className='text-muted-foreground'>
                          {formatTime(entry.created_at)}
                        </TableCell>
                        <TableCell className='text-muted-foreground'>
                          {entry.user_email ?? EMPTY_PLACEHOLDER}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant='outline'
                            className={
                              actionKindClassNames[actionKind(entry.action)]
                            }
                          >
                            {actionLabelKey(entry.action)
                              ? t(actionLabelKey(entry.action)!)
                              : entry.action}
                          </Badge>
                        </TableCell>
                        <TableCell className='text-muted-foreground'>
                          {entry.service_name ?? EMPTY_PLACEHOLDER}
                        </TableCell>
                        <TableCell
                          className='max-w-96 truncate text-muted-foreground'
                          // 详情可能被截断，完整内容靠悬停查看
                          title={entry.detail ?? undefined}
                        >
                          {entry.detail ?? EMPTY_PLACEHOLDER}
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={5} className='h-24 text-center'>
                        {t('audit.empty')}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>

            <div className='flex flex-wrap items-center justify-between gap-2 px-2'>
              <p className='text-sm text-muted-foreground'>
                {t('audit.total', { count: total })}
              </p>
              <div className='flex items-center gap-2'>
                <Select
                  value={`${pageSize}`}
                  onValueChange={(value) =>
                    updateSearch({ pageSize: Number(value) })
                  }
                >
                  <SelectTrigger
                    className='h-8 w-24'
                    aria-label={t('ui.table.rowsPerPage')}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent side='top'>
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <SelectItem key={size} value={`${size}`}>
                        {size}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className='text-sm text-muted-foreground'>
                  {t('ui.table.pageOf', { current: page, total: totalPages })}
                </span>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => updateSearch({ page: page - 1 })}
                  disabled={page <= 1 || logsQuery.isFetching}
                >
                  {t('ui.table.goPrev')}
                </Button>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => updateSearch({ page: page + 1 })}
                  disabled={page >= totalPages || logsQuery.isFetching}
                >
                  {t('ui.table.goNext')}
                </Button>
              </div>
            </div>
          </>
        )}
      </Main>
    </>
  )
}

/** 后端给的是 UTC ISO 串，按浏览器本地时区展示；解析失败时原样显示，不显示 Invalid Date */
function formatTime(value: string | null): string {
  if (!value) return EMPTY_PLACEHOLDER
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
