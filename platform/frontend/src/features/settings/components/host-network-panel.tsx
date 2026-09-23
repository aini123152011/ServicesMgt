import { useTranslation } from 'react-i18next'
import {
  type HostInterface,
  type HostNetworkCheck,
  type HostServiceBinding,
} from '@/api/system'
import { LEVEL_ICONS, levelTextClass } from '@/lib/host-network'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useHostNetworkQuery } from '../hooks/use-system'

function linkLabel(t: (key: string) => string, carrier: number | null): string {
  if (carrier === 1) return t('settings.about.hostNetwork.linkUp')
  if (carrier === 0) return t('settings.about.hostNetwork.linkDown')
  return t('settings.about.hostNetwork.linkUnknown')
}

/**
 * 宿主网口面板：物理网口清单 + 当前二层绑定 + 一致性校验结论。
 *
 * 为什么需要它：平台默认看不到宿主网口，而「DHCP 绑错网口」的失败表现是「服务健康、BMC 却拿不到
 * 地址」——最难排查的一类问题。这里把三件事一次摊开：有哪些口、哪块插了线、当前绑的是哪块，
 * 以及配置是否自洽（地址池是否在绑定口网段内、测试口是否误配了默认路由）。
 *
 * 判定口径：**carrier 才是「插了线」**，`UP` 不等于插线（实测 enp125s0f1/f2 是 UP 但 carrier=0）。
 */
export function HostNetworkPanel() {
  const { t } = useTranslation()
  const { data, isLoading } = useHostNetworkQuery()

  if (isLoading || !data) return null

  const interfaces: HostInterface[] = data.interfaces ?? []
  const checks: HostNetworkCheck[] = data.checks ?? []
  const bindings: HostServiceBinding[] = data.bindings ?? []
  const attached = bindings.filter((binding) => binding.attached)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.about.hostNetwork.title')}</CardTitle>
        <CardDescription>
          {t('settings.about.hostNetwork.description')}
        </CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-4 text-sm'>
        {data.ip_source !== 'ok' && (
          <p className='text-muted-foreground'>
            {t('settings.about.hostNetwork.ipUnavailable')}
          </p>
        )}

        <div className='rounded-md border'>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>
                  {t('settings.about.hostNetwork.columnName')}
                </TableHead>
                <TableHead>
                  {t('settings.about.hostNetwork.columnLink')}
                </TableHead>
                <TableHead>
                  {t('settings.about.hostNetwork.columnSpeed')}
                </TableHead>
                <TableHead>
                  {t('settings.about.hostNetwork.columnMac')}
                </TableHead>
                <TableHead>
                  {t('settings.about.hostNetwork.columnIp')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {interfaces.map((iface) => (
                <TableRow key={iface.name}>
                  <TableCell className='font-medium'>
                    {iface.name}
                    {iface.name === data.parent_iface && (
                      <Badge className='ms-2' variant='secondary'>
                        parent
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {iface.carrier === 1 ? (
                      <Badge variant='outline'>
                        {linkLabel(t, iface.carrier)}
                      </Badge>
                    ) : (
                      <span className='text-muted-foreground'>
                        {linkLabel(t, iface.carrier)}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {iface.speed_mbps ? `${iface.speed_mbps} Mb/s` : '—'}
                  </TableCell>
                  <TableCell className='font-mono text-xs'>
                    {iface.mac ?? '—'}
                  </TableCell>
                  <TableCell className='font-mono text-xs'>
                    {/* 显示「接口地址/前缀长度」而不是网络号（192.168.90.1/24 比 192.168.90.0/24 好认）；
                        IPv6 也一并列出——RA 前缀一致性校验依据的就是这些地址 */}
                    {(() => {
                      const shown = [
                        ...iface.ipv4.map((item) => {
                          const prefix = item.cidr.split('/')[1]
                          return prefix
                            ? `${item.address}/${prefix}`
                            : item.address
                        }),
                        ...iface.ipv6.map(
                          (item) => `${item.address}/${item.prefix}`
                        ),
                      ]
                      return shown.length > 0 ? shown.join(', ') : '—'
                    })()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <div>
          <p className='mb-1 font-medium'>
            {t('settings.about.hostNetwork.bindingTitle')}
          </p>
          {attached.length === 0 ? (
            <p className='text-muted-foreground'>
              {t('settings.about.hostNetwork.bindingNone')}
            </p>
          ) : (
            <ul className='space-y-1 text-muted-foreground'>
              {attached.map((binding) => (
                <li key={binding.service}>
                  <span className='text-foreground'>{binding.service}</span>
                  {' · '}
                  {binding.network}
                  {' · '}
                  {t('settings.about.hostNetwork.bindingPrefix')}=
                  {binding.parent ?? '—'}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <p className='mb-1 font-medium'>
            {t('settings.about.hostNetwork.checkTitle')}
          </p>
          <ul className='space-y-1'>
            {checks.map((check, index) => {
              const Icon = LEVEL_ICONS[check.level as keyof typeof LEVEL_ICONS]
              return (
                // key 带序号：同一 code 可能出现多条（如地址池起始/结束各一条）
                <li
                  key={`${check.code}-${index}`}
                  className='flex items-start gap-2'
                >
                  {Icon ? (
                    <Icon
                      className={`mt-0.5 size-4 shrink-0 ${levelTextClass(check.level)}`}
                    />
                  ) : (
                    <span className='mt-0.5 size-4 shrink-0 text-emerald-600'>
                      ✓
                    </span>
                  )}
                  <span className={levelTextClass(check.level)}>
                    {check.message}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>

        <div className='text-muted-foreground'>
          <p className='mb-1 font-medium text-foreground'>
            {t('settings.about.hostNetwork.enableTitle')}
          </p>
          <p>{t('settings.about.hostNetwork.enableHint')}</p>
          <pre className='mt-2 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs'>
            {`# .env
DHCP_PARENT_IFACE=${data.parent_iface || '<接 BMC 的网口>'}
L2_SUBNET=${data.l2_subnet || '192.168.90.0/24'}
L2_GATEWAY=192.168.90.1      # 宿主测试口地址；不给会让容器抢到 .1 造成地址冲突
L2_SERVICES=${data.l2_services.join(',') || 'dhcp,tftpd-hpa,rsyslog,chrony'}

# 只把 dhcp 挂到 macvlan；测试口配址必须 never-default
docker compose -f compose.yaml -f compose.l2.yaml up -d dhcp
nmcli con mod <测试口> ipv4.addresses 192.168.90.1/24 ipv4.gateway "" ipv4.never-default yes`}
          </pre>
        </div>
      </CardContent>
    </Card>
  )
}
