import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from '@/components/ui/form'
import { LocalizedFormMessage } from '@/components/localized-form-message'
import { PasswordInput } from '@/components/password-input'
import { useUpdatePasswordMutation } from '../hooks/use-account'

// 校验消息存 key（含确认密码这条纯前端规则），渲染处由 LocalizedFormMessage 翻译；
// 最小长度与后端 UpdatePassword 的 min_length=8 对齐，避免前端放行后被后端 422 拒绝
const passwordSchema = z
  .object({
    // 当前密码同样按后端 min_length=8 约束：短于 8 位必然不是既有密码，本地就能挡掉
    currentPassword: z
      .string()
      .min(8, 'settings.account.validation.passwordMin'),
    newPassword: z.string().min(8, 'settings.account.validation.passwordMin'),
    confirmPassword: z.string(),
  })
  .refine((values) => values.newPassword === values.confirmPassword, {
    message: 'settings.account.validation.passwordMismatch',
    path: ['confirmPassword'],
  })

type PasswordForm = z.infer<typeof passwordSchema>

/** 修改密码表单：成功后清空三个输入，不保留明文 */
export function AccountPasswordForm() {
  const { t } = useTranslation()
  const updatePassword = useUpdatePasswordMutation()

  const form = useForm<PasswordForm>({
    resolver: zodResolver(passwordSchema),
    defaultValues: {
      currentPassword: '',
      newPassword: '',
      confirmPassword: '',
    },
  })

  const onSubmit = (values: PasswordForm) => {
    updatePassword.mutate(
      {
        current_password: values.currentPassword,
        new_password: values.newPassword,
      },
      { onSuccess: () => form.reset() }
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.account.password.title')}</CardTitle>
        <CardDescription>
          {t('settings.account.password.description')}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form
            id='account-password-form'
            onSubmit={form.handleSubmit(onSubmit)}
            className='flex max-w-md flex-col gap-4'
          >
            <FormField
              control={form.control}
              name='currentPassword'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t('settings.account.password.current')}
                  </FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete='current-password' {...field} />
                  </FormControl>
                  <LocalizedFormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='newPassword'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('settings.account.password.new')}</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete='new-password' {...field} />
                  </FormControl>
                  <LocalizedFormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='confirmPassword'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t('settings.account.password.confirm')}
                  </FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete='new-password' {...field} />
                  </FormControl>
                  <LocalizedFormMessage />
                </FormItem>
              )}
            />
            <Button
              type='submit'
              className='self-start'
              disabled={updatePassword.isPending}
            >
              {updatePassword.isPending
                ? t('common.saving')
                : t('settings.account.password.submit')}
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  )
}
