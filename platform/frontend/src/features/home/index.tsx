import { Link, useNavigate } from '@tanstack/react-router'
import {
  Activity,
  AlertTriangle,
  CircleSlash,
  ListChecks,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Server,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { type AuditLogEntry } from '@/api/audit'
import { type ServiceStatusResponse, type ServiceSummary } from '@/api/services'
import { cn } from '@/lib/utils'
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
import {
  categoryLabelKeys,
  categoryTypes,
  reloadModeLabelKeys,
} from '@/features/services/data/data'
import {
  useServicesQuery,
  useServicesStatusQuery,
} from '@/features/services/hooks/use-services'
import { useAuditLogsQuery, useResetFaultModeMutation } from './hooks/use-home'

/** 缺失字段的占位符，避免单元格出现空白让人以为渲染出错 */
const EMPTY_PLACEHOLDER = '—'

/** Docker 健康检查里唯一表示正常的状态值，其余非空值都算异常 */
const HEALTHY = 'healthy'

/**
 * 首页：平台概览。
 *
 * 数据来自服务列表、各服务状态（15s 轮询）与审计日志；审计接口限 admin，
 * 非管理员不发起请求、也不渲染该卡片。
 */
export function Home() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { isAdmin } = usePermissions()
  const servicesQuery = useServicesQuery()
  const services = servicesQuery.data ?? []
  const statusQuery = useServicesStatusQuery(services.map((s) => s.name))
  const statuses = statusQuery.data ?? {}
  const auditQuery = useAuditLogsQuery(isAdmin)
  const resetFault = useResetFaultModeMutation()

  // 处于非 none 故障注入模式的服务：首页一眼可见，并可一键复位
  const faultingServices = services.filter(
    (service) => service.fault_mode && service.fault_mode !== 'none'
  )

  const handleResetFault = (serviceName: string) => {
    resetFault.mutate(serviceName, {
      onSuccess: () => toast.success(t('home.fault.resetDone')),
      onError: () => toast.error(t('home.fault.resetFailed')),
    })
  }

  const runningCount = services.filter(
    (service) => statuses[service.name]?.running === true
  ).length
  const stoppedCount = services.filter(
    (service) => statuses[service.name]?.running === false
  ).length
  const healthIssueCount = services.filter((service) => {
    const status = statuses[service.name]
    return (
      status?.running === true && !!status.health && status.health !== HEALTHY
    )
  }).length

  // 三项按状态汇总的统计何时显示骨架：服务列表还没回来时无从判断；
  // 列表为空时状态查询被禁用（isPending 会一直为 true），此时应显示 0 而不是永远转圈
  const statusPending =
    servicesQuery.isPending || (services.length > 0 && statusQuery.isPending)

  // 日志浏览指向日志监控类服务的详情页；不硬编码服务名，分类下没有服务时按钮禁用
  const logService = services.find(
    (service) => service.category === 'log-monitor'
  )

  return (
    <>
      <Header fixed>
        <HeaderActions />
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div>
          <h2 className='text-2xl font-bold tracking-tight'>
            {t('home.title')}
          </h2>
          <p className='text-muted-foreground'>{t('home.description')}</p>
        </div>

        {servicesQuery.isError ? (
          <div className='flex flex-col items-center gap-3 py-16'>
            <p className='text-muted-foreground'>{t('home.loadFailed')}</p>
            <Button variant='outline' onClick={() => servicesQuery.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : (
          <>
            <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-4'>
              <StatCard
                icon={Server}
                label={t('home.stats.services')}
                value={services.length}
                isPending={servicesQuery.isPending}
              />
              <StatCard
                icon={Activity}
                label={t('home.stats.running')}
                value={runningCount}
                isPending={statusPending}
              />
              <StatCard
                icon={AlertTriangle}
                label={t('home.stats.healthIssues')}
                value={healthIssueCount}
                isPending={statusPending}
                tone={healthIssueCount > 0 ? 'warning' : 'default'}
              />
              <StatCard
                icon={CircleSlash}
                label={t('home.stats.stopped')}
                value={stoppedCount}
                isPending={statusPending}
              />
            </div>

            <Card>
              <CardHeader>
                <CardTitle>{t('home.health.title')}</CardTitle>
                <CardDescription>
                  {t('home.health.description')}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ServiceHealthTable
                  services={services}
                  statuses={statuses}
                  isPending={servicesQuery.isPending}
                  onOpen={(name) =>
                    void navigate({
                      to: '/services/$serviceName',
                      params: { serviceName: name },
                    })
                  }
                />
              </CardContent>
            </Card>

            {/* 故障注入面板：测试仪器平台的核心状态，永远渲染（无异常时给出「全部正常」） */}
            <Card
              className={cn(
                faultingServices.length > 0 && 'border-destructive/40'
              )}
            >
              <CardHeader>
                <CardTitle>{t('home.fault.title')}</CardTitle>
                <CardDescription>{t('home.fault.description')}</CardDescription>
              </CardHeader>
              <CardContent className='space-y-2'>
                {faultingServices.length === 0 ? (
                  <p className='text-sm text-muted-foreground'>
                    {t('home.fault.none')}
                  </p>
                ) : (
                  faultingServices.map((service) => (
                    <div
                      key={service.name}
                      className='flex flex-wrap items-center gap-2'
                    >
                      <Link
                        to='/services/$serviceName'
                        params={{ serviceName: service.name }}
                        className='font-medium hover:underline'
                      >
                        {service.display_name}
                      </Link>
                      <Badge variant='destructive'>{service.fault_mode}</Badge>
                      <Button
                        size='sm'
                        variant='outline'
                        className='ms-auto'
                        disabled={resetFault.isPending}
                        onClick={() => handleResetFault(service.name)}
                      >
                        <RotateCcw />
                        {t('home.fault.reset')}
                      </Button>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>

            {/* 审计接口限 admin，非管理员既不发请求也不渲染卡片 */}
            {isAdmin && (
              <Card>
                <CardHeader>
                  <CardTitle>{t('home.recent.title')}</CardTitle>
                  <CardDescription>
                    {t('home.recent.description')}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <RecentChanges
                    entries={auditQuery.data ?? []}
                    isPending={auditQuery.isPending}
                  />
                </CardContent>
              </Card>
            )}

            <Card>
              <CardHeader>
                <CardTitle>{t('home.shortcuts.title')}</CardTitle>
                <CardDescription>
                  {t('home.shortcuts.description')}
                </CardDescription>
              </CardHeader>
              <CardContent className='flex flex-wrap gap-2'>
                <Button asChild variant='outline'>
                  <Link to='/services'>
                    <ListChecks />
                    {t('home.shortcuts.services')}
                  </Link>
                </Button>
                {logService ? (
                  <Button asChild variant='outline'>
                    <Link
                      to='/services/$serviceName'
                      params={{ serviceName: logService.name }}
                    >
                      <ScrollText />
                      {t('home.shortcuts.logs')}
                    </Link>
                  </Button>
                ) : (
                  <Button variant='outline' disabled>
                    <ScrollText />
                    {t('home.shortcuts.logs')}
                  </Button>
                )}
                {isAdmin && (
                  <Button asChild variant='outline'>
                    <Link to='/settings/updates'>
                      <RefreshCw />
                      {t('home.shortcuts.updates')}
                    </Link>
                  </Button>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </Main>
    </>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  isPending,
  tone = 'default',
}: {
  icon: LucideIcon
  label: string
  value: number
  isPending: boolean
  tone?: 'default' | 'warning'
}) {
  return (
    <Card>
      <CardHeader className='flex flex-row items-center justify-between gap-2 pb-2'>
        <CardDescription>{label}</CardDescription>
        <Icon
          className={cn(
            'size-4 shrink-0 text-muted-foreground',
            tone === 'warning' && 'text-destructive'
          )}
        />
      </CardHeader>
      <CardContent>
        {isPending ? (
          <Skeleton className='h-8 w-12' />
        ) : (
          <p
            className={cn(
              'text-3xl font-semibold tabular-nums',
              tone === 'warning' && 'text-destructive'
            )}
          >
            {value}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ServiceHealthTable({
  services,
  statuses,
  isPending,
  onOpen,
}: {
  services: ServiceSummary[]
  statuses: Record<string, ServiceStatusResponse>
  isPending: boolean
  onOpen: (name: string) => void
}) {
  const { t } = useTranslation()

  if (isPending) {
    return <Skeleton className='h-64 w-full' />
  }

  if (services.length === 0) {
    return (
      <p className='py-8 text-center text-muted-foreground'>
        {t('home.health.empty')}
      </p>
    )
  }

  return (
    <div className='overflow-x-auto'>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('home.health.name')}</TableHead>
            <TableHead>{t('home.health.category')}</TableHead>
            <TableHead>{t('home.health.status')}</TableHead>
            <TableHead>{t('home.health.healthCheck')}</TableHead>
            <TableHead>{t('home.health.reloadMode')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {services.map((service) => {
            const status = statuses[service.name]
            return (
              // 整行可点进详情；名称本身是链接，键盘与读屏用户也能进入
              <TableRow
                key={service.name}
                className='cursor-pointer'
                onClick={() => onOpen(service.name)}
              >
                <TableCell className='font-medium'>
                  <Link
                    to='/services/$serviceName'
                    params={{ serviceName: service.name }}
                    className='hover:underline'
                    onClick={(event) => event.stopPropagation()}
                  >
                    {service.display_name}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge
                    variant='outline'
                    className={categoryTypes[service.category]}
                  >
                    {t(categoryLabelKeys[service.category])}
                  </Badge>
                </TableCell>
                <TableCell>
                  <RunningBadge running={status?.running} />
                </TableCell>
                <TableCell>
                  <HealthBadge health={status?.health} />
                </TableCell>
                <TableCell className='text-muted-foreground'>
                  {t(reloadModeLabelKeys[service.reload_mode])}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

function RunningBadge({ running }: { running?: boolean }) {
  const { t } = useTranslation()

  if (running === undefined) {
    return <Badge variant='outline'>{t('services.status.unknown')}</Badge>
  }

  return running ? (
    <Badge>{t('services.status.running')}</Badge>
  ) : (
    <Badge variant='secondary'>{t('services.status.stopped')}</Badge>
  )
}

function HealthBadge({ health }: { health?: string | null }) {
  const { t } = useTranslation()

  if (!health) {
    // null 表示容器没有定义健康检查，与「状态未知」区分开
    return <Badge variant='outline'>{t('home.health.noCheck')}</Badge>
  }
  if (health === HEALTHY) {
    return <Badge>{t('home.health.healthy')}</Badge>
  }
  if (health === 'unhealthy') {
    return <Badge variant='destructive'>{t('home.health.unhealthy')}</Badge>
  }
  // Docker 健康检查的第三种取值 starting：首次探测未完成，不算异常
  return <Badge variant='secondary'>{t('home.health.starting')}</Badge>
}

function RecentChanges({
  entries,
  isPending,
}: {
  entries: AuditLogEntry[]
  isPending: boolean
}) {
  const { t } = useTranslation()

  if (isPending) {
    return <Skeleton className='h-48 w-full' />
  }

  if (entries.length === 0) {
    return (
      <p className='py-8 text-center text-muted-foreground'>
        {t('home.recent.empty')}
      </p>
    )
  }

  return (
    <div className='overflow-x-auto'>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('home.recent.time')}</TableHead>
            <TableHead>{t('home.recent.action')}</TableHead>
            <TableHead>{t('home.recent.target')}</TableHead>
            <TableHead>{t('home.recent.operator')}</TableHead>
            <TableHead>{t('home.recent.detail')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.map((entry) => (
            <TableRow key={entry.id}>
              <TableCell className='whitespace-nowrap text-muted-foreground'>
                {formatTime(entry.created_at)}
              </TableCell>
              <TableCell className='font-mono text-xs'>
                {entry.action}
              </TableCell>
              <TableCell>{entry.service_name ?? EMPTY_PLACEHOLDER}</TableCell>
              <TableCell className='text-muted-foreground'>
                {entry.user_email ?? EMPTY_PLACEHOLDER}
              </TableCell>
              <TableCell className='text-muted-foreground'>
                {entry.detail ?? EMPTY_PLACEHOLDER}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** 后端给的是 UTC ISO 串，按浏览器本地时区展示；解析失败时原样显示，不显示 Invalid Date */
function formatTime(value: string | null): string {
  if (!value) return EMPTY_PLACEHOLDER
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
