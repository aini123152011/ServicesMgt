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
import { type ServiceAction } from '@/api/services'
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
import { Main } from '@/components/layout/main'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { categoryLabels, categoryTypes, reloadModeLabels } from '../data/data'
import {
  useServiceActionMutation,
  useServiceQuery,
  useServiceStatusQuery,
} from '../hooks/use-services'
import { ServiceConfigForm } from './service-config-form'
import { ServiceLogsDialog } from './service-logs-dialog'
import { ServiceStatusDot } from './service-status-dot'

type ServiceDetailProps = {
  /** 服务名（路由参数，与 manifest.yaml 的 name 一致） */
  name: string
}

export function ServiceDetail({ name }: ServiceDetailProps) {
  const [logsOpen, setLogsOpen] = useState(false)
  const { isOperator } = usePermissions()
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
            <p className='text-muted-foreground'>服务详情加载失败。</p>
            <Button variant='outline' onClick={() => serviceQuery.refetch()}>
              重试
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
    { action: 'start', label: '启动', icon: Play },
    { action: 'stop', label: '停止', icon: Square },
    { action: 'restart', label: '重启', icon: RotateCw },
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
              返回列表
            </Link>
          </Button>
          <Separator orientation='vertical' className='h-5!' />
          <h2 className='text-2xl font-bold tracking-tight'>
            {manifest.display_name}
          </h2>
          <Badge variant='outline' className={categoryTypes[manifest.category]}>
            {categoryLabels[manifest.category]}
          </Badge>
          <span className='text-sm text-muted-foreground'>
            {manifest.name} · 容器 {manifest.container_name} ·{' '}
            {reloadModeLabels[manifest.reload_mode]}
          </span>
        </div>

        {/* 状态条：running/health 由 useServiceStatusQuery 每 5s 轮询刷新 */}
        <div className='flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border p-4'>
          <ServiceStatusDot name={name} withLabel />
          <span className='text-sm text-muted-foreground'>
            容器状态：
            {statusQuery.data?.status ?? '未知'}
          </span>
          <span className='text-sm text-muted-foreground'>
            健康检查：
            {statusQuery.data?.health ?? '—'}
          </span>
          <Button
            variant='outline'
            size='sm'
            className='ms-auto'
            onClick={() => setLogsOpen(true)}
          >
            <ScrollText />
            查看日志
          </Button>
        </div>

        {/* 生命周期：readonly 角色整体禁用；运行中禁启动，反之禁停止/重启 */}
        <div
          className='flex gap-2'
          title={isOperator ? undefined : '只读角色无权操作'}
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

        <Card>
          <CardHeader>
            <CardTitle>服务配置</CardTitle>
            <CardDescription>
              配置目录 {manifest.config_dir} · 配置文件{' '}
              {manifest.config_files.join('、') || '—'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ServiceConfigForm
              key={`${name}-${config.rendered_at ?? 'none'}`}
              name={name}
              fields={schema.fields}
              config={config}
            />
          </CardContent>
        </Card>
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
  return (
    <Header fixed>
      <span className='text-sm text-muted-foreground'>服务详情 / {name}</span>
      <Search className='me-auto' />
      <ThemeSwitch />
      <ProfileDropdown />
    </Header>
  )
}
