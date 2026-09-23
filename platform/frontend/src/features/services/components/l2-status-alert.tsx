import { useTranslation } from 'react-i18next'
import { type HostNetworkCheck } from '@/api/system'
import { LEVEL_ICONS, topLevel } from '@/lib/host-network'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useHostNetworkQuery } from '@/features/settings/hooks/use-system'

type L2StatusAlertProps = {
  /** 服务名（路由参数）：用于筛出「与该服务相关」的校验结论 */
  name: string
}

/**
 * 二层测试网段状态提示：只显示与该服务相关（或全局）且需要人注意的校验结论。
 *
 * 为什么放在服务详情页：配错网口的失败表现是「服务健康、BMC 却拿不到地址」，排查时人就在这个页面。
 * `ok` 级结论不显示（正常状态不该占版面），没有需要关注的问题时整块不渲染。
 * 平台侧对宿主网口有 30s 缓存 + 轮询，接好线后刷新即可看到 carrier 变化。
 */
export function L2StatusAlert({ name }: L2StatusAlertProps) {
  const { t } = useTranslation()
  const { data } = useHostNetworkQuery()

  const relevant: HostNetworkCheck[] = (data?.checks ?? []).filter(
    (check) =>
      check.level !== 'ok' &&
      (check.service === null || check.service === name) &&
      // 与二层无关的全局提示（未启用）只在该服务属于二层服务集合时才显示，避免污染其他服务页
      (check.code !== 'l2_not_configured' ||
        (data?.l2_services ?? []).includes(name))
  )
  if (relevant.length === 0) return null

  const level = topLevel(relevant)
  const Icon = LEVEL_ICONS[level as keyof typeof LEVEL_ICONS]

  return (
    <Alert variant={level === 'error' ? 'destructive' : 'default'}>
      <Icon />
      <AlertTitle>{t('services.l2Status.title')}</AlertTitle>
      <AlertDescription>
        <ul className='list-disc space-y-1 ps-4'>
          {relevant.map((check, index) => (
            // key 带序号：同一 code 可能出现多条（如地址池起始/结束各一条）
            <li key={`${check.code}-${index}`}>{check.message}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  )
}
