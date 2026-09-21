import { useEffect } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useTranslation } from 'react-i18next'
import { type UserSelfUpdatePayload } from '@/api/users'
import { useAuthStore } from '@/stores/auth-store'
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
import { Input } from '@/components/ui/input'
import { LocalizedFormMessage } from '@/components/localized-form-message'
import { useUpdateProfileMutation } from '../hooks/use-account'

// 消息存 key：schema 在模块级创建，那时取不到最新语言，渲染处由 LocalizedFormMessage 翻译
const profileSchema = z.object({
  fullName: z.string().max(255, 'settings.account.validation.fullNameMax'),
  email: z.email({ error: 'settings.account.validation.email' }),
})

type ProfileForm = z.infer<typeof profileSchema>

/**
 * 个人资料表单：改姓名与邮箱。
 *
 * 默认值来自 auth store（登录后水合的用户），改完由 mutation 写回 store；
 * 用户对象变化时同步表单，避免其他入口改了资料后这里还显示旧值。
 */
export function AccountProfileForm() {
  const { t } = useTranslation()
  const user = useAuthStore((state) => state.auth.user)
  const updateProfile = useUpdateProfileMutation()

  const form = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      fullName: user?.full_name ?? '',
      email: user?.email ?? '',
    },
  })

  const { reset } = form
  useEffect(() => {
    reset({ fullName: user?.full_name ?? '', email: user?.email ?? '' })
  }, [user, reset])

  const onSubmit = (values: ProfileForm) => {
    // 后端 full_name 可空：清空输入即提交 null，而不是空串
    const payload: UserSelfUpdatePayload = {
      full_name: values.fullName.trim() === '' ? null : values.fullName.trim(),
      email: values.email,
    }
    updateProfile.mutate(payload)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.account.profile.title')}</CardTitle>
        <CardDescription>
          {t('settings.account.profile.description')}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form
            id='account-profile-form'
            onSubmit={form.handleSubmit(onSubmit)}
            className='flex max-w-md flex-col gap-4'
          >
            <FormField
              control={form.control}
              name='fullName'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t('settings.account.profile.fullName')}
                  </FormLabel>
                  <FormControl>
                    <Input
                      placeholder={t(
                        'settings.account.profile.fullNamePlaceholder'
                      )}
                      autoComplete='name'
                      {...field}
                    />
                  </FormControl>
                  <LocalizedFormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='email'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('settings.account.profile.email')}</FormLabel>
                  <FormControl>
                    <Input
                      type='email'
                      placeholder={t(
                        'settings.account.profile.emailPlaceholder'
                      )}
                      autoComplete='email'
                      {...field}
                    />
                  </FormControl>
                  <LocalizedFormMessage />
                </FormItem>
              )}
            />
            <Button
              type='submit'
              className='self-start'
              disabled={updateProfile.isPending}
            >
              {updateProfile.isPending
                ? t('common.saving')
                : t('settings.account.profile.submit')}
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  )
}
