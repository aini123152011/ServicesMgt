import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Trans, useTranslation } from 'react-i18next'
import { type UserPublic } from '@/api/auth'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { roleMeta } from '../data/data'
import { useDeleteUserMutation } from '../hooks/use-users'

type UserDeleteDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentRow: UserPublic
}

export function UsersDeleteDialog({
  open,
  onOpenChange,
  currentRow,
}: UserDeleteDialogProps) {
  const [value, setValue] = useState('')
  const deleteMutation = useDeleteUserMutation()
  const { t } = useTranslation()

  const handleDelete = () => {
    if (value.trim() !== currentRow.email) return
    deleteMutation.mutate(currentRow.id, {
      onSuccess: () => onOpenChange(false),
    })
  }

  const roleNames = currentRow.roles
    .map((role) => {
      const meta = roleMeta(role)
      return meta.labelKey ? t(meta.labelKey) : (meta.label ?? role)
    })
    .join('、')

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      form='users-delete-form'
      disabled={value.trim() !== currentRow.email || deleteMutation.isPending}
      title={
        <span className='text-destructive'>
          <AlertTriangle
            className='me-1 inline-block stroke-destructive'
            size={18}
          />{' '}
          {t('users.deleteTitle')}
        </span>
      }
      desc={
        <form
          id='users-delete-form'
          onSubmit={(e) => {
            e.preventDefault()
            handleDelete()
          }}
          className='space-y-4'
        >
          {/* 整句放资源文件里，只有邮箱与角色名是插值，翻译时不会拆散语序 */}
          <p className='mb-2'>
            <Trans
              i18nKey='users.deleteDesc'
              values={{ email: currentRow.email, roles: roleNames }}
              components={{ b: <span className='font-bold' />, br: <br /> }}
            />
          </p>

          <Label className='my-2'>
            {t('users.deleteEmailLabel')}
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={t('users.deleteEmailPlaceholder')}
              autoFocus
            />
          </Label>

          <Alert variant='destructive'>
            <AlertTitle>{t('users.deleteAlertTitle')}</AlertTitle>
            <AlertDescription>{t('users.deleteAlertDesc')}</AlertDescription>
          </Alert>
        </form>
      }
      confirmText={t('users.actionDelete')}
      destructive
    />
  )
}
