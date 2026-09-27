import { useTranslation } from 'react-i18next'
import { type ServicePort } from '@/api/services'
import { Badge } from '@/components/ui/badge'

type ServicePortListProps = {
  ports: ServicePort[]
  /** 该服务的二层测试网段地址；给了就提示「在网段上用容器端口」 */
  l2Address?: string | null
}

/**
 * 状态条上的端口一览：容器端口 + 宿主机发布端口。
 *
 * 为什么两个都给：测试人员第一件事是「往哪个地址哪个端口打」，而容器端口在管理网上
 * 不通（nginx 容器 80 发布在宿主 18102，宿主 80 上跑的是别的应用），只写容器端口会让人
 * 打错地方；只写发布端口则解释不了「配置里填的端口」与「外面看到的端口」为何不同。
 */
export function ServicePortList({ ports, l2Address }: ServicePortListProps) {
  const { t } = useTranslation()
  if (ports.length === 0) return null
  // 全部端口都是同号发布时没有箭头可解释，别挂一句对不上的提示
  const hasPublished = ports.some(
    (port) => port.host_port != null && port.host_port !== port.port
  )

  return (
    <span className='flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground'>
      {t('services.detail.ports')}
      {ports.map((port) => (
        <Badge
          key={`${port.port}/${port.protocol}`}
          variant='outline'
          className='font-mono text-xs font-normal'
          title={port.description ?? undefined}
        >
          {port.port}/{port.protocol}
          {port.host_port && port.host_port !== port.port
            ? ` → ${port.host_port}`
            : ''}
        </Badge>
      ))}
      {(l2Address || hasPublished) && (
        <span className='text-xs'>
          {l2Address
            ? t('services.detail.portsL2Hint', { address: l2Address })
            : t('services.detail.portsHint')}
        </span>
      )}
    </span>
  )
}
