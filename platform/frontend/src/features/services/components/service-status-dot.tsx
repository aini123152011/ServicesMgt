import { cn } from '@/lib/utils'
import { useServiceStatusQuery } from '../hooks/use-services'

type ServiceStatusDotProps = {
  name: string
  withLabel?: boolean
  className?: string
}

/**
 * 服务运行状态圆点（含可选文案），总览卡片与详情页状态条共用。
 * 状态查询失败或加载中显示"状态未知"，不阻断页面其余内容。
 */
export function ServiceStatusDot({
  name,
  withLabel = false,
  className,
}: ServiceStatusDotProps) {
  const statusQuery = useServiceStatusQuery(name)
  const running = statusQuery.data?.running

  const label = running ? '运行中' : running === false ? '已停止' : '状态未知'

  return (
    <span className={cn('inline-flex items-center gap-2 text-sm', className)}>
      <span
        className={cn(
          'size-2 shrink-0 rounded-full',
          running && 'animate-pulse bg-emerald-500',
          running === false && 'bg-zinc-400 dark:bg-zinc-600',
          running === undefined && 'bg-amber-500'
        )}
      />
      {withLabel && (
        <span className={cn(!running && 'text-muted-foreground')}>{label}</span>
      )}
    </span>
  )
}
