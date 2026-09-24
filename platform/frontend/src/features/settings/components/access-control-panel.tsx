import { useState } from 'react'
import { Loader2, Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  type AccessRule,
  type AccessRuleKind,
  type AccessRuleListType,
} from '@/api/access-control'
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  useAccessControlSettingsQuery,
  useAccessRulesQuery,
  useCreateAccessRuleMutation,
  useDeleteAccessRuleMutation,
  useUpdateAccessControlSettingsMutation,
} from '../hooks/use-access-control'

/**
 * 访问控制面板：自助注册开关 + 邮箱域名规则 + 来源 IP 规则。
 *
 * **防自锁由服务端兜底**：保存 IP 规则时后端会先用新规则评估当前请求 IP，不通过就拒绝写入
 * 并返回原因，错误经全局拦截器展示。前端不再单独调一次 preflight——真正起保护作用的是
 * 保存时的那道校验，重复实现一遍只会多一处可能与后端不一致的判定。
 *
 * 面板本身对所有登录用户可见（能看到规则），但所有写操作要求 admin：与 L2 面板同一口径。
 */
export function AccessControlPanel() {
  const { t } = useTranslation()
  const { isAdmin } = usePermissions()
  const settingsQuery = useAccessControlSettingsQuery()
  const settingsMutation = useUpdateAccessControlSettingsMutation()

  const settings = settingsQuery.data
  const registrationEnabled = settings?.registration_enabled ?? true

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.accessControl.title')}</CardTitle>
        <CardDescription>
          {t('settings.accessControl.description')}
        </CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-6 text-sm'>
        {settings && !settings.email_configured && (
          <p className='rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200'>
            {t('settings.accessControl.emailNotConfigured')}
          </p>
        )}

        <div className='flex flex-col gap-2 rounded-md border p-3'>
          <div className='flex items-center justify-between gap-4'>
            <div>
              <Label htmlFor='registration-enabled'>
                {t('settings.accessControl.registrationLabel')}
              </Label>
              <p className='text-muted-foreground'>
                {t('settings.accessControl.registrationHint')}
              </p>
            </div>
            <Button
              id='registration-enabled'
              variant={registrationEnabled ? 'secondary' : 'outline'}
              disabled={!isAdmin || settingsMutation.isPending || !settings}
              onClick={() =>
                settingsMutation.mutate({
                  registration_enabled: !registrationEnabled,
                })
              }
            >
              {settingsMutation.isPending && (
                <Loader2 className='animate-spin' />
              )}
              {registrationEnabled ? t('common.enabled') : t('common.disabled')}
            </Button>
          </div>
        </div>

        <RuleSection
          kind='email_suffix'
          title={t('settings.accessControl.emailRulesTitle')}
          hint={t('settings.accessControl.emailRulesHint')}
          placeholder={t('settings.accessControl.valuePlaceholderEmail')}
          isAdmin={isAdmin}
        />
        <RuleSection
          kind='ip'
          title={t('settings.accessControl.ipRulesTitle')}
          hint={t('settings.accessControl.ipRulesHint')}
          placeholder={t('settings.accessControl.valuePlaceholderIp')}
          isAdmin={isAdmin}
        />
      </CardContent>
    </Card>
  )
}

interface RuleSectionProps {
  kind: AccessRuleKind
  title: string
  hint: string
  placeholder: string
  isAdmin: boolean
}

/** 一类规则的列表 + 新增（列表类型/值/备注） */
function RuleSection({
  kind,
  title,
  hint,
  placeholder,
  isAdmin,
}: RuleSectionProps) {
  const { t } = useTranslation()
  const rulesQuery = useAccessRulesQuery(kind)
  const createMutation = useCreateAccessRuleMutation(kind)
  const deleteMutation = useDeleteAccessRuleMutation(kind)

  const [listType, setListType] = useState<AccessRuleListType>('deny')
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')

  const rules: AccessRule[] = rulesQuery.data ?? []

  function submit() {
    const trimmed = value.trim()
    if (!trimmed) return
    createMutation.mutate(
      {
        kind,
        list_type: listType,
        value: trimmed,
        note: note.trim() ? note.trim() : null,
      },
      {
        onSuccess: () => {
          setValue('')
          setNote('')
        },
      }
    )
  }

  return (
    <div className='flex flex-col gap-3'>
      <div>
        <p className='font-medium'>{title}</p>
        <p className='text-muted-foreground'>{hint}</p>
      </div>

      <div className='rounded-md border'>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className='w-24'>
                {t('settings.accessControl.listTypeLabel')}
              </TableHead>
              <TableHead>{t('settings.accessControl.valueLabel')}</TableHead>
              <TableHead>{t('settings.accessControl.noteLabel')}</TableHead>
              <TableHead className='w-16' />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rules.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={4}
                  className='text-center text-muted-foreground'
                >
                  {t('settings.accessControl.empty')}
                </TableCell>
              </TableRow>
            ) : (
              rules.map((rule) => (
                <TableRow key={rule.id}>
                  <TableCell>
                    <Badge
                      variant='outline'
                      className={
                        rule.list_type === 'deny'
                          ? 'border-destructive/40 text-destructive'
                          : 'border-emerald-300 text-emerald-700 dark:text-emerald-300'
                      }
                    >
                      {rule.list_type === 'deny'
                        ? t('settings.accessControl.denyLabel')
                        : t('settings.accessControl.allowLabel')}
                    </Badge>
                  </TableCell>
                  <TableCell className='font-mono text-xs'>
                    {rule.value}
                  </TableCell>
                  <TableCell className='text-muted-foreground'>
                    {rule.note || '—'}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant='ghost'
                      size='icon'
                      disabled={!isAdmin || deleteMutation.isPending}
                      title={t('common.delete')}
                      onClick={() => deleteMutation.mutate(rule.id)}
                    >
                      <Trash2 className='size-4' />
                      <span className='sr-only'>{t('common.delete')}</span>
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className='flex flex-wrap items-end gap-2'>
        <div className='grid gap-1'>
          <Label htmlFor={`${kind}-list-type`}>
            {t('settings.accessControl.listTypeLabel', {
              defaultValue: '类型',
            })}
          </Label>
          <Select
            value={listType}
            onValueChange={(next) => setListType(next as AccessRuleListType)}
          >
            <SelectTrigger id={`${kind}-list-type`} className='w-28'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='deny'>
                {t('settings.accessControl.denyLabel')}
              </SelectItem>
              <SelectItem value='allow'>
                {t('settings.accessControl.allowLabel')}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className='grid flex-1 gap-1'>
          <Label htmlFor={`${kind}-value`}>
            {t('settings.accessControl.valueLabel')}
          </Label>
          <Input
            id={`${kind}-value`}
            value={value}
            placeholder={placeholder}
            className='font-mono'
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                submit()
              }
            }}
          />
        </div>
        <div className='grid flex-1 gap-1'>
          <Label htmlFor={`${kind}-note`}>
            {t('settings.accessControl.noteLabel', { defaultValue: '备注' })}
          </Label>
          <Input
            id={`${kind}-note`}
            value={note}
            placeholder={t('settings.accessControl.notePlaceholder')}
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
        <Button
          disabled={!isAdmin || createMutation.isPending || !value.trim()}
          onClick={submit}
        >
          {createMutation.isPending ? (
            <Loader2 className='animate-spin' />
          ) : (
            <Plus />
          )}
          {t('settings.accessControl.add')}
        </Button>
      </div>
    </div>
  )
}
