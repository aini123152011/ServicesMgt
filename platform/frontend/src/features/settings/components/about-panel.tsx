import { useTranslation } from 'react-i18next'
import { Logo } from '@/assets/logo'
import { Badge } from '@/components/ui/badge'
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
import { taskStatusKey } from '../data/update-status'
import { useSystemInfoQuery } from '../hooks/use-system'

/** 更新目标里的平台自身条目，不计入「纳管服务」清单 */
const PLATFORM_TARGET = 'platform'

/**
 * 关于：平台版本与构建、部署形态、纳管服务清单与开源组件说明。
 *
 * 数据全部来自 GET /system/info（登录即可读，不限 admin），
 * 因此这个分节对只读用户同样有内容；许可一节只描述所用开源项目，不臆造许可证清单。
 */
export function AboutPanel() {
  const { t } = useTranslation()
  const { data, isLoading } = useSystemInfoQuery()
  const status = data?.status ?? null
  // 平台自身不在「纳管服务」清单里，它由上面的版本卡片描述
  const services = (data?.targets ?? []).filter(
    (target) => target.target !== PLATFORM_TARGET
  )

  return (
    <div className='flex flex-col gap-4'>
      {/* 标记比页面主标题小一号，只做身份提示，不与主标题抢视线 */}
      <div className='flex items-center gap-2 text-muted-foreground'>
        <Logo className='size-5' />
        <span className='text-sm font-medium'>{t('common.app.name')}</span>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.about.version.title')}</CardTitle>
          <CardDescription>
            {t('settings.about.version.description')}
          </CardDescription>
        </CardHeader>
        <CardContent className='grid gap-2 text-sm sm:grid-cols-2'>
          <div>
            <span className='text-muted-foreground'>
              {t('settings.about.version.platform')}:{' '}
            </span>
            {isLoading ? (
              <Skeleton className='inline-block h-4 w-16' />
            ) : (
              data?.version
            )}
          </div>
          <div>
            <span className='text-muted-foreground'>
              {t('settings.about.version.build')}:{' '}
            </span>
            {data?.build || '—'}
          </div>
          <div>
            <span className='text-muted-foreground'>
              {t('settings.about.version.registry')}:{' '}
            </span>
            {data?.update_registry || t('settings.about.version.registryUnset')}
          </div>
          <div>
            <span className='text-muted-foreground'>
              {t('settings.about.version.lastTask')}:{' '}
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.about.deployment.title')}</CardTitle>
          <CardDescription>
            {t('settings.about.deployment.description')}
          </CardDescription>
        </CardHeader>
        <CardContent className='grid gap-2 text-sm'>
          <div>
            <span className='text-muted-foreground'>
              {t('settings.about.deployment.shape')}:{' '}
            </span>
            {t('settings.about.deployment.shapeValue')}
          </div>
          <div>
            <span className='text-muted-foreground'>
              {t('settings.about.deployment.docker')}:{' '}
            </span>
            {data
              ? data.docker_available
                ? t('settings.about.deployment.dockerAvailable')
                : t('settings.about.deployment.dockerUnavailable')
              : '—'}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.about.services.title')}</CardTitle>
          <CardDescription>
            {t('settings.about.services.description')}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className='h-32 w-full' />
          ) : services.length === 0 ? (
            <p className='py-6 text-center text-muted-foreground'>
              {t('settings.about.services.empty')}
            </p>
          ) : (
            <div className='overflow-x-auto'>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('settings.about.services.name')}</TableHead>
                    <TableHead>{t('settings.about.services.image')}</TableHead>
                    <TableHead>
                      {t('settings.about.services.running')}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {services.map((target) => (
                    <TableRow key={target.target}>
                      <TableCell className='font-medium'>
                        {target.display_name}
                        <span className='ms-1 text-xs text-muted-foreground'>
                          ({target.target})
                        </span>
                      </TableCell>
                      <TableCell className='font-mono text-xs'>
                        {target.image}
                      </TableCell>
                      <TableCell>
                        {target.container_running ? (
                          <Badge>
                            {t('settings.about.services.runningYes')}
                          </Badge>
                        ) : (
                          <Badge variant='secondary'>
                            {t('settings.about.services.runningNo')}
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.about.license.title')}</CardTitle>
          <CardDescription>
            {t('settings.about.license.description')}
          </CardDescription>
        </CardHeader>
        <CardContent className='flex flex-col gap-2 text-sm text-muted-foreground'>
          <p>{t('settings.about.license.frontend')}</p>
          <p>{t('settings.about.license.backend')}</p>
        </CardContent>
      </Card>
    </div>
  )
}
