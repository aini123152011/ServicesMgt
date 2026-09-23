import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type L2ApplyResult, type L2ConfigPayload } from '@/api/l2'
import { LEVEL_ICONS, levelTextClass } from '@/lib/host-network'
import { usePermissions } from '@/hooks/use-permissions'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  useL2ApplyMutation,
  useL2DisableMutation,
  useL2PreflightMutation,
  useL2StatusQuery,
} from '../hooks/use-l2'

/**
 * 二层夹具绑定：启用 / 切换 / 停用。
 *
 * 为什么放在页面上：换线或换网段原来要登机器改 .env 再叠加 compose override，现场很容易漏步骤；
 * 这里把三件事一次做完（写 .env、重建 macvlan 网络、可选同步 dhcp 服务配置），并且**执行前先干跑**
 * 把「将要改什么」摆出来——危险选择（父口承载默认路由、网口没链路）会被后端拦下。
 *
 * 唯一仍需人工的一步是宿主测试口配址（tftpd/rsyslog/chrony 要靠它被 BMC 访问），页面只给命令。
 */
export function L2BindingPanel() {
  const { t } = useTranslation()
  const { isAdmin } = usePermissions()
  const { data, isLoading } = useL2StatusQuery()
  const preflight = useL2PreflightMutation()
  const apply = useL2ApplyMutation()
  const disable = useL2DisableMutation()

  // 表单值派生自状态 + 用户草稿：状态刷新时用新值预填，用户改过的字段以草稿为准
  // （不用 effect 回填——那会在每次轮询回来时把用户正在输入的内容冲掉）
  const [draft, setDraft] = useState<Partial<L2ConfigPayload>>({})
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [disableOpen, setDisableOpen] = useState(false)
  const [result, setResult] = useState<L2ApplyResult | null>(null)

  if (isLoading || !data) return null

  const form: L2ConfigPayload = {
    parent_iface: draft.parent_iface ?? data.parent_iface,
    l2_subnet: draft.l2_subnet ?? data.l2_subnet,
    l2_gateway: draft.l2_gateway ?? data.l2_gateway,
    l2_subnet_v6: draft.l2_subnet_v6 ?? data.l2_subnet_v6,
    l2_gateway_v6: draft.l2_gateway_v6 ?? data.l2_gateway_v6,
    sync_service_config: draft.sync_service_config ?? true,
  }

  const update = (patch: Partial<L2ConfigPayload>) => {
    setDraft((prev) => ({ ...prev, ...patch }))
  }

  const startPreflight = () => {
    setResult(null)
    preflight.mutate(form, {
      onSuccess: (data) => {
        if (data.ok) setConfirmOpen(true)
      },
    })
  }

  const confirmApply = () => {
    setConfirmOpen(false)
    apply.mutate(form, { onSuccess: setResult })
  }

  const preflightData = preflight.data
  const busy = apply.isPending || disable.isPending || preflight.isPending

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.about.l2.title')}</CardTitle>
        <CardDescription>{t('settings.about.l2.description')}</CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-4 text-sm'>
        {!data.env_available && (
          <Alert>
            <AlertDescription>
              {t('settings.about.l2.envUnavailable', { error: data.env_error })}
            </AlertDescription>
          </Alert>
        )}

        {data.duplicate_keys.length > 0 && (
          <Alert>
            <AlertDescription>
              {t('settings.about.l2.duplicateKeys', {
                keys: data.duplicate_keys.join(', '),
              })}
            </AlertDescription>
          </Alert>
        )}

        <div className='flex flex-wrap items-center gap-2'>
          <Badge variant={data.enabled ? 'default' : 'secondary'}>
            {data.enabled
              ? t('settings.about.l2.enabled')
              : t('settings.about.l2.disabled')}
          </Badge>
          {data.network && (
            <span className='font-mono text-xs text-muted-foreground'>
              {data.network}
              {data.network_parent ? ` · parent=${data.network_parent}` : ''}
              {data.address ? ` · ${data.address}` : ''}
              {data.address_v6 ? ` · ${data.address_v6}` : ''}
            </span>
          )}
        </div>

        {data.drift.length > 0 && (
          <Alert variant='destructive'>
            <AlertTitle>{t('settings.about.l2.driftTitle')}</AlertTitle>
            <AlertDescription>
              <ul className='list-disc space-y-1 ps-4'>
                {data.drift.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        <ul className='space-y-1'>
          {data.checks.map((check, index) => {
            const Icon = LEVEL_ICONS[check.level as keyof typeof LEVEL_ICONS]
            return (
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

        <div className='grid gap-3 sm:grid-cols-2'>
          <div className='flex flex-col gap-1.5'>
            <Label htmlFor='l2-parent'>{t('settings.about.l2.parent')}</Label>
            <Select
              value={form.parent_iface || undefined}
              onValueChange={(value) => update({ parent_iface: value })}
              disabled={!isAdmin || busy}
            >
              <SelectTrigger id='l2-parent' className='w-full'>
                <SelectValue
                  placeholder={t('settings.about.l2.parentPlaceholder')}
                />
              </SelectTrigger>
              <SelectContent>
                {data.candidates.map((candidate) => (
                  <SelectItem
                    key={candidate.name}
                    value={candidate.name}
                    disabled={!candidate.selectable && !candidate.is_parent}
                  >
                    {candidate.name}
                    {candidate.addresses.length > 0
                      ? ` · ${candidate.addresses.join(', ')}`
                      : ''}
                    {candidate.reason ? ` · ${candidate.reason}` : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className='flex flex-col gap-1.5'>
            <Label htmlFor='l2-subnet'>{t('settings.about.l2.subnet')}</Label>
            <Input
              id='l2-subnet'
              value={form.l2_subnet}
              onChange={(event) => update({ l2_subnet: event.target.value })}
              placeholder='192.168.90.0/24'
              disabled={!isAdmin || busy}
            />
          </div>

          <div className='flex flex-col gap-1.5'>
            <Label htmlFor='l2-gateway'>{t('settings.about.l2.gateway')}</Label>
            <Input
              id='l2-gateway'
              value={form.l2_gateway}
              onChange={(event) => update({ l2_gateway: event.target.value })}
              placeholder='192.168.90.1'
              disabled={!isAdmin || busy}
            />
          </div>

          <div className='flex flex-col gap-1.5'>
            <Label htmlFor='l2-subnet-v6'>
              {t('settings.about.l2.subnetV6')}
            </Label>
            <Input
              id='l2-subnet-v6'
              value={form.l2_subnet_v6}
              onChange={(event) => update({ l2_subnet_v6: event.target.value })}
              placeholder='fd00:90::/64'
              disabled={!isAdmin || busy}
            />
          </div>

          <div className='flex flex-col gap-1.5'>
            <Label htmlFor='l2-gateway-v6'>
              {t('settings.about.l2.gatewayV6')}
            </Label>
            <Input
              id='l2-gateway-v6'
              value={form.l2_gateway_v6}
              onChange={(event) =>
                update({ l2_gateway_v6: event.target.value })
              }
              placeholder='fd00:90::1'
              disabled={!isAdmin || busy}
            />
          </div>

          <div className='flex items-center gap-2 pt-6'>
            <Checkbox
              id='l2-sync-config'
              checked={form.sync_service_config}
              onCheckedChange={(checked) =>
                update({ sync_service_config: checked === true })
              }
              disabled={!isAdmin || busy}
            />
            <Label htmlFor='l2-sync-config' className='font-normal'>
              {t('settings.about.l2.syncServiceConfig')}
            </Label>
          </div>
        </div>

        <div className='flex flex-wrap gap-2'>
          <Button
            onClick={startPreflight}
            disabled={!isAdmin || busy || !form.parent_iface}
          >
            {t('settings.about.l2.apply')}
          </Button>
          <Button
            variant='outline'
            onClick={() => {
              setResult(null)
              setDisableOpen(true)
            }}
            disabled={!isAdmin || busy || !data.enabled}
          >
            {t('settings.about.l2.disable')}
          </Button>
          {!isAdmin && (
            <span className='self-center text-muted-foreground'>
              {t('settings.about.l2.adminOnly')}
            </span>
          )}
        </div>

        {preflightData && !preflightData.ok && (
          <Alert variant='destructive'>
            <AlertTitle>{t('settings.about.l2.blockedTitle')}</AlertTitle>
            <AlertDescription>
              <ul className='list-disc space-y-1 ps-4'>
                {preflightData.blocking.map((item, index) => (
                  <li key={`${item.code}-${index}`}>{item.message}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        {result && (
          <Alert variant={result.applied ? 'default' : 'destructive'}>
            <AlertTitle>
              {result.applied
                ? t('settings.about.l2.resultOk')
                : result.rolled_back
                  ? t('settings.about.l2.resultRolledBack')
                  : t('settings.about.l2.resultFailed')}
            </AlertTitle>
            <AlertDescription>
              {result.message && <p className='mb-1'>{result.message}</p>}
              <ol className='list-decimal space-y-1 ps-4'>
                {result.steps.map((step, index) => (
                  <li key={`${index}-${step}`}>{step}</li>
                ))}
              </ol>
            </AlertDescription>
          </Alert>
        )}

        {data.nmcli_commands.length > 0 && (
          <div className='text-muted-foreground'>
            <p className='mb-1 font-medium text-foreground'>
              {t('settings.about.l2.nmcliTitle')}
            </p>
            <p>{t('settings.about.l2.nmcliHint')}</p>
            <pre className='mt-2 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs'>
              {data.nmcli_commands.join('\n')}
            </pre>
          </div>
        )}
      </CardContent>

      <AlertDialog open={disableOpen} onOpenChange={setDisableOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('settings.about.l2.disableConfirmTitle')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('settings.about.l2.disableConfirmBody')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                disable.mutate(undefined, { onSuccess: setResult })
              }
            >
              {t('settings.about.l2.confirmAction')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('settings.about.l2.confirmTitle')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('settings.about.l2.confirmBody')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {preflightData && (
            <div className='text-sm'>
              <ol className='list-decimal space-y-1 ps-4'>
                {preflightData.steps.map((step, index) => (
                  <li key={`${index}-${step}`}>{step}</li>
                ))}
              </ol>
              {Object.keys(preflightData.service_config).length > 0 && (
                <div className='mt-3'>
                  <p className='font-medium'>
                    {t('settings.about.l2.confirmConfigTitle')}
                  </p>
                  <pre className='mt-1 overflow-x-auto rounded-md bg-muted p-2 font-mono text-xs'>
                    {Object.entries(preflightData.service_config)
                      .map(([key, value]) => `${key} = ${value}`)
                      .join('\n')}
                  </pre>
                </div>
              )}
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={confirmApply}>
              {t('settings.about.l2.confirmAction')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
