import { DotsHorizontalIcon } from '@radix-ui/react-icons'
import { type Row } from '@tanstack/react-table'
import { MailCheck, Trash2, UserPen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { type UserPublic } from '@/api/auth'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useVerifyUserEmailMutation } from '../hooks/use-users'
import { useUsers } from './users-provider'

type DataTableRowActionsProps = {
  row: Row<UserPublic>
}

export function DataTableRowActions({ row }: DataTableRowActionsProps) {
  const { setOpen, setCurrentRow } = useUsers()
  const verifyEmailMutation = useVerifyUserEmailMutation()
  const { t } = useTranslation()
  // 只有未验证的账号才需要人工放行：已验证的账号再点一次没有意义
  const needsManualVerify = row.original.email_verified_at === null
  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant='ghost'
            className='flex h-8 w-8 p-0 data-[state=open]:bg-muted'
          >
            <DotsHorizontalIcon className='h-4 w-4' />
            <span className='sr-only'>{t('users.actionEdit')}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align='end' className='w-40'>
          <DropdownMenuItem
            onClick={() => {
              setCurrentRow(row.original)
              setOpen('edit')
            }}
          >
            {t('users.actionEdit')}
            <DropdownMenuShortcut>
              <UserPen size={16} />
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          {needsManualVerify && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => verifyEmailMutation.mutate(row.original.id)}
              >
                {t('users.actionVerifyEmail')}
                <DropdownMenuShortcut>
                  <MailCheck size={16} />
                </DropdownMenuShortcut>
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={() => {
              setCurrentRow(row.original)
              setOpen('delete')
            }}
            className='text-red-500!'
          >
            {t('users.actionDelete')}
            <DropdownMenuShortcut>
              <Trash2 size={16} />
            </DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )
}
