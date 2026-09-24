import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ArrowLeft,
  LoaderCircle,
  Play,
  RotateCw,
  ScrollText,
  Square,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { type ServiceAction } from '@/api/services'
import { serviceDisplayName } from '@/lib/service-name'
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
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/layout/header-actions'
import { Main } from '@/components/layout/main'
import {
  categoryLabelKeys,
  categoryTypes,
  reloadModeLabelKeys,
} from '../data/data'
import {
  useServiceActionMutation,
  useServiceQuery,
  useServiceStatusQuery,
} from '../hooks/use-services'
import { L2StatusAlert } from './l2-status-alert'
import { ServiceConfigForm } from './service-config-form'
import { ServiceConfigHistory } from './service-config-history'
import { ServiceDataExplorer } from './service-data-explorer'
import { ServiceLogsDialog } from './service-logs-dialog'
import { ServiceStatusDot } from './service-status-dot'
import { ServiceUsageCard } from './service-usage-card'

type ServiceDetailProps = {
  /** 服务名（路由参数，与 manifest.yaml 的 name 一致） */
  name: string
}

/** 配置页签：受控在详情页，保存触发重挂载时才能留在当前页签 */
type ConfigTab = 'base' | 'fault'

export function ServiceDetail({ name }: ServiceDetailProps) {
  const [logsOpen, setLogsOpen] = useState(false)
  // 页签状态必须放在这里：保存配置会失效 query、改变 config.rendered_at，
  // 进而让 ServiceConfigForm 的 key 变化而重挂载；状态若在表单内部会被重置回「基础配置」
  const [configTab, setConfigTab] = useState<ConfigTab>('base')
  const { isOperator } = usePermissions()
  const { t } = useTranslation()
  const serviceQuery = useServiceQuery(name)
  const statusQuery = useServiceStatusQuery(name)
  const actionMutation = useServiceActionMutation(name)

  if (serviceQuery.isPending) {
    return (
      <>
        <ServiceDetailHeader name={name} />
        <Main className='flex flex-1 flex-col gap-4'>
          <Skeleton className='h-10 w-64' />
          <Skeleton className='h-16 w-full' />
          <Skeleton className='h-96 w-full' />
        </Main>
      </>
    )
  }

  if (serviceQuery.isError) {
    return (
      <>
        <ServiceDetailHeader name={name} />
        <Main>
          <div className='flex flex-col items-center gap-3 py-16'>
            <p className='text-muted-foreground'>
              {t('services.detail.loadFailed')}
            </p>
            <Button variant='outline' onClick={() => serviceQuery.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        </Main>
      </>
    )
  }

  const { manifest, schema, config } = serviceQuery.data
  const running = statusQuery.data?.running ?? false
  // variables 记录正在执行的动作，用于只在对应按钮上显示转圈
  const pendingAction = actionMutation.isPending
    ? actionMutation.variables
    : null

  const lifecycleActions: {
    action: ServiceAction
    label: string
    icon: React.ElementType
  }[] = [
    { action: 'start', label: t('services.action.start'), icon: Play },
    { action: 'stop', label: t('services.action.stop'), icon: Square },
    { action: 'restart', label: t('services.action.restart'), icon: RotateCw },
  ]

  return (
    <>
      <ServiceDetailHeader name={name} />
      <Main className='flex flex-1 flex-col gap-4'>
        <div className='flex flex-wrap items-center gap-3'>
          <Button
            variant='ghost'
            size='sm'
            className='text-muted-foreground'
            asChild
          >
            <Link to='/services'>
              <ArrowLeft />
              {t('services.detail.backToList')}
            </Link>
          </Button>
          <Separator orientation='vertical' className='h-5!' />
          <h2 className='text-2xl font-bold tracking-tight'>
            {serviceDisplayName(manifest)}
          </h2>
          <Badge variant='outline' className={categoryTypes[manifest.category]}>
            {t(categoryLabelKeys[manifest.category])}
          </Badge>
          <span className='text-sm text-muted-foreground'>
            {manifest.name} ·{' '}
            {t('services.detail.container', { name: manifest.container_name })}{' '}
            · {t(reloadModeLabelKeys[manifest.reload_mode])}
          </span>
        </div>

        {/* 状态条：running/health 由 useServiceStatusQuery 每 5s 轮询刷新 */}
        <div className='flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border p-4'>
          <ServiceStatusDot name={name} withLabel />
          <span className='text-sm text-muted-foreground'>
            {t('services.detail.containerStatus')}
            {statusQuery.data?.status ?? t('common.unknown')}
          </span>
          <span className='text-sm text-muted-foreground'>
            {t('services.detail.health')}
            {statusQuery.data?.health ?? '—'}
          </span>
          <Button
            variant='outline'
            size='sm'
            className='ms-auto'
            onClick={() => setLogsOpen(true)}
          >
            <ScrollText />
            {t('services.detail.viewLogs')}
          </Button>
        </div>

        {/* 生命周期：readonly 角色整体禁用；运行中禁启动，反之禁停止/重启 */}
        <div
          className='flex gap-2'
          title={isOperator ? undefined : t('services.detail.readonlyAction')}
        >
          {lifecycleActions.map(({ action, label, icon: Icon }) => {
            const disabled =
              !isOperator ||
              actionMutation.isPending ||
              (action === 'start' ? running : !running)
            return (
              <Button
                key={action}
                variant={action === 'stop' ? 'destructive' : 'default'}
                disabled={disabled}
                onClick={() => actionMutation.mutate(action)}
              >
                {pendingAction === action ? (
                  <LoaderCircle className='animate-spin' />
                ) : (
                  <Icon />
                )}
                {label}
              </Button>
            )
          })}
        </div>

        {/* 二层网段状态：绑错网口/池不在网段的失败表现是「服务健康但 BMC 拿不到地址」，
            排查时人就在这个页面，所以放在最前面 */}
        <L2StatusAlert name={name} />

        {/* 外部使用方式：测试人员最先需要「怎么连」，放在配置卡片之前 */}
        <ServiceUsageCard
          entries={manifest.usage}
          port={manifest.ports[0]?.port}
          l2Address={serviceQuery.data.l2_address}
        />

        <Card>
          <CardHeader>
            <CardTitle>{t('services.detail.configTitle')}</CardTitle>
            <CardDescription>
              {t('services.detail.configDir', { dir: manifest.config_dir })} ·{' '}
              {t('services.detail.configFiles', {
                files: manifest.config_files.join('、') || '—',
              })}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ServiceConfigForm
              key={`${name}-${config.rendered_at ?? 'none'}`}
              name={name}
              fields={schema.fields}
              config={config}
              activeTab={configTab}
              onTabChange={setConfigTab}
            />
          </CardContent>
        </Card>

        {/* 配置历史：与配置卡片相邻，改完配置可直接看到版本与回滚入口 */}
        <ServiceConfigHistory name={name} fields={schema.fields} />

        {manifest.data_dir && (
          <ServiceDataExplorer name={name} dataDir={manifest.data_dir} />
        )}
      </Main>

      <ServiceLogsDialog
        name={name}
        open={logsOpen}
        onOpenChange={setLogsOpen}
      />
    </>
  )
}

// 路由头部：详情加载各分支共用，避免重复排版
function ServiceDetailHeader({ name }: { name: string }) {
  const { t } = useTranslation()
  return (
    <Header fixed>
      <span className='text-sm text-muted-foreground'>
        {t('nav.serviceDetail')} / {name}
      </span>
      <HeaderActions />
    </Header>
  )
}
