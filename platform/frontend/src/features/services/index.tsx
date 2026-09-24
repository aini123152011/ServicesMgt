import { Link } from '@tanstack/react-router'
import { Network } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { type ServiceSummary } from '@/api/services'
import { serviceDisplayName } from '@/lib/service-name'
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
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/layout/header-actions'
import { Main } from '@/components/layout/main'
import { ServiceStatusDot } from './components/service-status-dot'
import {
  categoryIcons,
  categoryLabelKeys,
  categoryTypes,
  reloadModeLabelKeys,
} from './data/data'
import { useServicesQuery } from './hooks/use-services'

export function Services() {
  const servicesQuery = useServicesQuery()
  const services = servicesQuery.data ?? []
  const { t } = useTranslation()

  return (
    <>
      <Header fixed>
        <HeaderActions />
      </Header>

      <Main className='flex flex-1 flex-col gap-4'>
        <div>
          <h2 className='text-2xl font-bold tracking-tight'>
            {t('services.list.title')}
          </h2>
          <p className='text-muted-foreground'>
            {t('services.list.description')}
          </p>
        </div>

        {servicesQuery.isPending ? (
          // 骨架屏：与卡片网格同构，避免加载完成时跳动
          <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-3'>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className='h-56 rounded-xl' />
            ))}
          </div>
        ) : servicesQuery.isError ? (
          <div className='flex flex-col items-center gap-3 py-16'>
            <p className='text-muted-foreground'>
              {t('services.list.loadFailed')}
            </p>
            <Button variant='outline' onClick={() => servicesQuery.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : services.length === 0 ? (
          <div className='flex flex-col items-center gap-2 py-16'>
            <p className='text-muted-foreground'>{t('services.list.empty')}</p>
          </div>
        ) : (
          <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-3'>
            {services.map((service) => (
              <ServiceCard key={service.name} service={service} />
            ))}
          </div>
        )}
      </Main>
    </>
  )
}

function ServiceCard({ service }: { service: ServiceSummary }) {
  const CategoryIcon = categoryIcons[service.category]
  const { t } = useTranslation()

  return (
    <Link
      to='/services/$serviceName'
      params={{ serviceName: service.name }}
      className='block h-full'
    >
      <Card className='h-full transition-colors hover:border-primary/50'>
        <CardHeader>
          <div className='flex items-start justify-between gap-2'>
            <CardTitle className='flex items-center gap-2'>
              <CategoryIcon className='size-4 text-muted-foreground' />
              {serviceDisplayName(service)}
            </CardTitle>
            <Badge
              variant='outline'
              className={categoryTypes[service.category]}
            >
              {t(categoryLabelKeys[service.category])}
            </Badge>
          </div>
          <CardDescription>{service.description}</CardDescription>
        </CardHeader>
        <CardContent className='space-y-3 text-sm'>
          <ServiceStatusDot name={service.name} withLabel />
          <div className='flex flex-wrap items-center gap-1.5'>
            {service.ports.map((p) => (
              // title 悬停展示端口说明，卡片空间有限
              <Badge key={`${p.port}/${p.protocol}`} variant='secondary'>
                <Network className='size-3' />
                <span title={p.description ?? undefined}>
                  {p.port}/{p.protocol}
                </span>
              </Badge>
            ))}
          </div>
          <p className='text-muted-foreground'>
            {t(reloadModeLabelKeys[service.reload_mode])}
          </p>
        </CardContent>
      </Card>
    </Link>
  )
}
