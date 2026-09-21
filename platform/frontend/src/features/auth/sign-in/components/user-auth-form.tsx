import { useState } from 'react'
import { z } from 'zod'
import { AxiosError } from 'axios'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useNavigate } from '@tanstack/react-router'
import { Loader2, LogIn } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { getCurrentUser, loginAccessToken } from '@/api/auth'
import { useAuthStore } from '@/stores/auth-store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { LocalizedFormMessage } from '@/components/localized-form-message'
import { PasswordInput } from '@/components/password-input'

// 消息存 key：schema 在模块级创建，那时取不到最新语言，渲染处由 LocalizedFormMessage 翻译
const formSchema = z.object({
  email: z.email({
    error: (iss) =>
      iss.input === ''
        ? 'auth.validation.emailRequired'
        : 'auth.validation.emailInvalid',
  }),
  password: z
    .string()
    .min(1, 'auth.validation.passwordRequired')
    .min(7, 'auth.validation.passwordMin'),
})

interface UserAuthFormProps extends React.HTMLAttributes<HTMLFormElement> {
  redirectTo?: string
}

export function UserAuthForm({
  className,
  redirectTo,
  ...props
}: UserAuthFormProps) {
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()
  const { auth } = useAuthStore()
  const { t } = useTranslation()

  /** 后端失败响应统一带 { detail }，优先展示给用户 */
  function resolveErrorMessage(error: unknown): string {
    if (error instanceof AxiosError) {
      const detail = error.response?.data?.detail
      if (typeof detail === 'string' && detail.length > 0) {
        return detail
      }
    }
    return t('auth.signIn.failed')
  }

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      email: '',
      password: '',
    },
  })

  function onSubmit(data: z.infer<typeof formSchema>) {
    setIsLoading(true)

    const signIn = async () => {
      try {
        const token = await loginAccessToken(data.email, data.password)
        // 先落 token，请求 /users/me 时拦截器才能附带 Authorization
        auth.setAccessToken(token.access_token)
        const user = await getCurrentUser()
        auth.setUser(user)

        // 回跳地址可能是带查询串的路径，解析后交给 navigate 保证路由匹配
        const target = redirectTo
          ? new URL(redirectTo, window.location.origin)
          : null
        const search = target
          ? Object.fromEntries(target.searchParams)
          : undefined
        navigate({
          to: target?.pathname ?? '/',
          search: search && Object.keys(search).length > 0 ? search : undefined,
          replace: true,
        })

        return t('auth.signIn.success', { email: user.email })
      } catch (error) {
        // 任一步失败都清掉半套凭证，避免出现"有 token 无 user"的中间态
        auth.reset()
        throw error
      }
    }

    toast.promise(signIn(), {
      loading: t('auth.signIn.loading'),
      success: (message) => {
        setIsLoading(false)
        return message
      },
      error: (error) => {
        setIsLoading(false)
        return resolveErrorMessage(error)
      },
    })
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        className={cn('grid gap-3', className)}
        {...props}
      >
        <FormField
          control={form.control}
          name='email'
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('auth.emailLabel')}</FormLabel>
              <FormControl>
                <Input placeholder='name@example.com' {...field} />
              </FormControl>
              <LocalizedFormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name='password'
          render={({ field }) => (
            <FormItem className='relative'>
              <FormLabel>{t('auth.passwordLabel')}</FormLabel>
              <FormControl>
                <PasswordInput placeholder='********' {...field} />
              </FormControl>
              <LocalizedFormMessage />
              <Link
                to='/forgot-password'
                className='absolute inset-e-0 -top-0.5 text-sm font-medium text-muted-foreground hover:opacity-75'
              >
                {t('auth.signIn.forgotPassword')}
              </Link>
            </FormItem>
          )}
        />
        <Button className='mt-2' disabled={isLoading}>
          {isLoading ? <Loader2 className='animate-spin' /> : <LogIn />}
          {t('auth.signIn.submit')}
        </Button>
      </form>
    </Form>
  )
}
