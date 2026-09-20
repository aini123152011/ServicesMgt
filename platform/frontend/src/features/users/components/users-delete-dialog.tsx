import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
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

  const handleDelete = () => {
    if (value.trim() !== currentRow.email) return
    deleteMutation.mutate(currentRow.id, {
      onSuccess: () => onOpenChange(false),
    })
  }

  const roleNames = currentRow.roles.map((role) => roleMeta(role).label)

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
          删除用户
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
          <p className='mb-2'>
            确定要删除 <span className='font-bold'>{currentRow.email}</span>？
            <br />
            该用户（角色：
            <span className='font-bold'>{roleNames.join('、')}</span>
            ）将被永久移除，此操作不可撤销。
          </p>

          <Label className='my-2'>
            邮箱：
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder='输入邮箱确认删除'
              autoFocus
            />
          </Label>

          <Alert variant='destructive'>
            <AlertTitle>注意！</AlertTitle>
            <AlertDescription>删除后无法恢复，请谨慎操作。</AlertDescription>
          </Alert>
        </form>
      }
      confirmText='删除'
      destructive
    />
  )
}
