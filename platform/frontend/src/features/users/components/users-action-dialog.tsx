import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useTranslation } from 'react-i18next'
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
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { LocalizedFormMessage } from '@/components/localized-form-message'
import { PasswordInput } from '@/components/password-input'
import { isKnownRole, roleMeta } from '../data/data'
import {
  useCreateUserMutation,
  useRolesQuery,
  useUpdateUserMutation,
} from '../hooks/use-users'

// 校验消息存 key：schema 在模块级创建，那时取不到最新语言，渲染处由 LocalizedFormMessage 翻译
const formSchema = z
  .object({
    email: z.email({
      error: (iss) =>
        iss.input === ''
          ? 'users.validation.emailRequired'
          : 'users.validation.emailInvalid',
    }),
    fullName: z.string(),
    // 编辑时留空表示不修改密码
    password: z.string().transform((pwd) => pwd.trim()),
    confirmPassword: z.string().transform((pwd) => pwd.trim()),
    roles: z.array(z.enum(ROLE_NAMES)).min(1, 'users.validation.rolesRequired'),
    isActive: z.boolean(),
    isEdit: z.boolean(),
  })
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return password.length > 0
    },
    {
      message: 'users.validation.passwordRequired',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return password.length >= 8
    },
    {
      message: 'users.validation.passwordMin',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return /[a-z]/.test(password)
    },
    {
      message: 'users.validation.passwordLowercase',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return /\d/.test(password)
    },
    {
      message: 'users.validation.passwordDigit',
      path: ['password'],
    }
  )
  .refine(
    ({ isEdit, password, confirmPassword }) => {
      if (isEdit && !password) return true
      return password === confirmPassword
    },
    {
      message: 'users.validation.passwordMismatch',
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
  const { t } = useTranslation()
  // 角色选项以 GET /roles 返回为准；契约外角色名不进入多选，避免表单值越出 z.enum
  const roleChoices = (rolesQuery.data ?? []).flatMap((role) => {
    if (!isKnownRole(role)) return []
    const meta = roleMeta(role)
    return [{ value: role, label: meta.labelKey ? t(meta.labelKey) : role }]
  })
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
          <DialogTitle>{isEdit ? t('users.edit') : t('users.new')}</DialogTitle>
          <DialogDescription>
            {isEdit ? t('users.editDesc') : t('users.createDesc')}
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
                    <FormLabel className='col-span-2 text-end'>
                      {t('users.field.email')}
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder='name@example.com'
                        className='col-span-4'
                        autoComplete='off'
                        {...field}
                      />
                    </FormControl>
                    <LocalizedFormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='fullName'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>
                      {t('users.field.fullName')}
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={t('users.field.fullNamePlaceholder')}
                        className='col-span-4'
                        autoComplete='off'
                        {...field}
                      />
                    </FormControl>
                    <LocalizedFormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='password'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>
                      {t('users.field.password')}
                    </FormLabel>
                    <FormControl>
                      <PasswordInput
                        placeholder={
                          isEdit
                            ? t('users.field.passwordEditPlaceholder')
                            : t('users.field.passwordNewPlaceholder')
                        }
                        className='col-span-4'
                        autoComplete='new-password'
                        {...field}
                      />
                    </FormControl>
                    <LocalizedFormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='confirmPassword'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>
                      {t('users.field.confirmPassword')}
                    </FormLabel>
                    <FormControl>
                      <PasswordInput
                        disabled={!isPasswordTouched}
                        placeholder={t(
                          'users.field.confirmPasswordPlaceholder'
                        )}
                        className='col-span-4'
                        autoComplete='new-password'
                        {...field}
                      />
                    </FormControl>
                    <LocalizedFormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='roles'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 pt-2 text-end'>
                      {t('users.field.roles')}
                    </FormLabel>
                    <div className='col-span-4 space-y-1'>
                      {rolesQuery.isPending ? (
                        <p className='text-sm text-muted-foreground'>
                          {t('users.rolesLoading')}
                        </p>
                      ) : roleChoices.length === 0 ? (
                        <p className='text-sm text-muted-foreground'>
                          {t('users.rolesLoadFailed')}
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
                      <LocalizedFormMessage />
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
                        <FormLabel>{t('users.field.active')}</FormLabel>
                        <FormDescription>
                          {t('users.field.activeHint')}
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
            {isSaving ? t('common.saving') : t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
