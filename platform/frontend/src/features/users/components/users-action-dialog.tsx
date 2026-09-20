import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { type UserPublic } from '@/api/auth'
import { ROLE_NAMES, type UserUpdatePayload } from '@/api/users'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { PasswordInput } from '@/components/password-input'
import { isKnownRole, roleMeta } from '../data/data'
import {
  useCreateUserMutation,
  useRolesQuery,
  useUpdateUserMutation,
} from '../hooks/use-users'

const formSchema = z
  .object({
    email: z.email({
      error: (iss) => (iss.input === '' ? '请输入邮箱。' : undefined),
    }),
    fullName: z.string(),
    // 编辑时留空表示不修改密码
    password: z.string().transform((pwd) => pwd.trim()),
    confirmPassword: z.string().transform((pwd) => pwd.trim()),
    roles: z.array(z.enum(ROLE_NAMES)).min(1, '至少选择一个角色。'),
    isActive: z.boolean(),
    isEdit: z.boolean(),
  })
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return password.length > 0
    },
    {
      message: '请输入密码。',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return password.length >= 8
    },
    {
      message: '密码长度至少 8 个字符。',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return /[a-z]/.test(password)
    },
    {
      message: '密码需至少包含一个小写字母。',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return /\d/.test(password)
    },
    {
      message: '密码需至少包含一个数字。',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password, confirmPassword }) => {
      if (isEdit && !password) return true
      return password === confirmPassword
    },
    {
      message: '两次输入的密码不一致。',
      path: ['confirmPassword'],
    }
  )
type UserForm = z.infer<typeof formSchema>

type UserActionDialogProps = {
  currentRow?: UserPublic
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function UsersActionDialog({
  currentRow,
  open,
  onOpenChange,
}: UserActionDialogProps) {
  const isEdit = !!currentRow
  const rolesQuery = useRolesQuery()
  // 角色选项以 GET /roles 返回为准；契约外角色名不进入多选，避免表单值越出 z.enum
  const roleChoices = (rolesQuery.data ?? []).flatMap((role) =>
    isKnownRole(role) ? [{ value: role, label: roleMeta(role).label }] : []
  )
  const createUserMutation = useCreateUserMutation()
  const updateUserMutation = useUpdateUserMutation()
  const isSaving = createUserMutation.isPending || updateUserMutation.isPending

  const form = useForm<UserForm>({
    resolver: zodResolver(formSchema),
    defaultValues: isEdit
      ? {
          email: currentRow.email,
          fullName: currentRow.full_name ?? '',
          password: '',
          confirmPassword: '',
          // 过滤契约外角色名，避免表单值越出三角色枚举
          roles: currentRow.roles.filter(
            (role): role is UserForm['roles'][number] =>
              ROLE_NAMES.some((name) => name === role)
          ),
          isActive: currentRow.is_active,
          isEdit,
        }
      : {
          email: '',
          fullName: '',
          password: '',
          confirmPassword: '',
          // 与后端缺省语义一致：未显式选择时落 readonly
          roles: ['readonly'],
          isActive: true,
          isEdit,
        },
  })

  const onSubmit = (values: UserForm) => {
    if (isEdit && currentRow) {
      // PATCH 只传可变字段；留空密码不提交，避免覆盖原密码
      const payload: UserUpdatePayload = {
        email: values.email,
        full_name: values.fullName === '' ? null : values.fullName,
        is_active: values.isActive,
        roles: values.roles,
      }
      if (values.password) payload.password = values.password
      updateUserMutation.mutate(
        { id: currentRow.id, payload },
        {
          onSuccess: () => {
            form.reset()
            onOpenChange(false)
          },
        }
      )
    } else {
      createUserMutation.mutate(
        {
          email: values.email,
          password: values.password,
          full_name: values.fullName === '' ? undefined : values.fullName,
          roles: values.roles,
        },
        {
          onSuccess: () => {
            form.reset()
            onOpenChange(false)
          },
        }
      )
    }
  }

  const isPasswordTouched = !!form.formState.dirtyFields.password

  return (
    <Dialog
      open={open}
      onOpenChange={(state) => {
        form.reset()
        onOpenChange(state)
      }}
    >
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader className='text-start'>
          <DialogTitle>{isEdit ? '编辑用户' : '新建用户'}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? '修改用户信息、角色或激活状态，保存后立即生效。'
              : '创建平台用户并分配角色，创建后可用邮箱加密码登录。'}
          </DialogDescription>
        </DialogHeader>
        <div className='h-105 w-[calc(100%+0.75rem)] overflow-y-auto py-1 pe-3'>
          <Form {...form}>
            <form
              id='user-form'
              onSubmit={form.handleSubmit(onSubmit)}
              className='space-y-4 px-0.5'
            >
              <FormField
                control={form.control}
                name='email'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>邮箱</FormLabel>
                    <FormControl>
                      <Input
                        placeholder='name@example.com'
                        className='col-span-4'
                        autoComplete='off'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='fullName'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>姓名</FormLabel>
                    <FormControl>
                      <Input
                        placeholder='选填'
                        className='col-span-4'
                        autoComplete='off'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='password'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>密码</FormLabel>
                    <FormControl>
                      <PasswordInput
                        placeholder={
                          isEdit
                            ? '留空表示不修改密码'
                            : '至少 8 位，含小写字母和数字'
                        }
                        className='col-span-4'
                        autoComplete='new-password'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='confirmPassword'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>
                      确认密码
                    </FormLabel>
                    <FormControl>
                      <PasswordInput
                        disabled={!isPasswordTouched}
                        placeholder='再次输入密码'
                        className='col-span-4'
                        autoComplete='new-password'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='roles'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 pt-2 text-end'>
                      角色
                    </FormLabel>
                    <div className='col-span-4 space-y-1'>
                      {rolesQuery.isPending ? (
                        <p className='text-sm text-muted-foreground'>
                          角色列表加载中…
                        </p>
                      ) : roleChoices.length === 0 ? (
                        <p className='text-sm text-muted-foreground'>
                          角色列表加载失败，请关闭后重试。
                        </p>
                      ) : (
                        <div className='flex flex-col gap-2'>
                          {roleChoices.map((option) => (
                            <FormItem
                              key={option.value}
                              className='flex items-center gap-2 space-y-0'
                            >
                              <FormControl>
                                <Checkbox
                                  checked={field.value.includes(option.value)}
                                  onCheckedChange={(checked) => {
                                    field.onChange(
                                      checked
                                        ? [...field.value, option.value]
                                        : field.value.filter(
                                            (value) => value !== option.value
                                          )
                                    )
                                  }}
                                />
                              </FormControl>
                              <FormLabel className='text-sm font-normal'>
                                {option.label}
                              </FormLabel>
                            </FormItem>
                          ))}
                        </div>
                      )}
                      <FormMessage />
                    </div>
                  </FormItem>
                )}
              />
              {isEdit && (
                <FormField
                  control={form.control}
                  name='isActive'
                  render={({ field }) => (
                    <FormItem className='col-span-6 flex flex-row items-center justify-between rounded-lg border p-3'>
                      <div className='space-y-0.5'>
                        <FormLabel>激活状态</FormLabel>
                        <FormDescription>
                          停用后该用户将无法登录平台
                        </FormDescription>
                      </div>
                      <FormControl>
                        <Switch
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />
              )}
            </form>
          </Form>
        </div>
        <DialogFooter>
          <Button type='submit' form='user-form' disabled={isSaving}>
            {isSaving ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
