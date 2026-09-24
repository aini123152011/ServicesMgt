import { useEffect, useState } from 'react'
import { z } from 'zod'
import { AxiosError } from 'axios'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useNavigate } from '@tanstack/react-router'
import { Loader2, LogIn } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  getCurrentUser,
  getRegistrationAvailability,
  loginAccessToken,
} from '@/api/auth'
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
import { CaptchaField } from '../../components/captcha-field'
import { useCaptcha } from '../../hooks/use-captcha'

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
  const [captchaAnswer, setCaptchaAnswer] = useState('')
  const [captchaRefreshToken, setCaptchaRefreshToken] = useState(0)
  const captcha = useCaptcha('login', captchaRefreshToken)
  const [registrationOpen, setRegistrationOpen] = useState(false)
  const navigate = useNavigate()
  const { auth } = useAuthStore()
  const { t } = useTranslation()

  // 注册入口是否显示由服务端决定（管理员可关闭注册）；取不到就不显示，避免给出死链接
  useEffect(() => {
    let active = true
    getRegistrationAvailability()
      .then((availability) => {
        if (active) {
          setRegistrationOpen(availability.enabled)
        }
      })
      .catch(() => {
        // 登录页不该因为一个附属信息请求失败而报错
      })
    return () => {
      active = false
    }
  }, [])

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

  /** 未验证账号：直接把人带到验证码页（那里能重发验证码），比只弹一句错误有用 */
  function isUnverifiedError(error: unknown): boolean {
    return (
      error instanceof AxiosError &&
      error.response?.data?.detail === 'Email is not verified'
    )
  }

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      email: '',
      password: '',
    },
  })

  /** 换一张并清空输入：票据一次性，失败后不换图用户会一直卡在「验证码错误」 */
  function refreshCaptcha() {
    setCaptchaRefreshToken((token) => token + 1)
    setCaptchaAnswer('')
  }

  function onSubmit(data: z.infer<typeof formSchema>) {
    setIsLoading(true)
    const email = data.email

    const signIn = async () => {
      try {
        const token = await loginAccessToken(email, data.password, {
          captchaId: captcha.captchaId,
          captchaAnswer: captchaAnswer,
        })
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
        refreshCaptcha()
        if (isUnverifiedError(error)) {
          navigate({ to: '/otp', search: { email }, replace: true })
        }
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
        return isUnverifiedError(error)
          ? t('auth.signIn.unverified')
          : resolveErrorMessage(error)
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
        <CaptchaField
          image={captcha.image}
          isLoading={captcha.isLoading}
          isError={captcha.isError}
          answer={captchaAnswer}
          onAnswerChange={setCaptchaAnswer}
          onRefresh={refreshCaptcha}
        />
        <Button className='mt-2' disabled={isLoading}>
          {isLoading ? <Loader2 className='animate-spin' /> : <LogIn />}
          {t('auth.signIn.submit')}
        </Button>
        {registrationOpen && (
          <p className='text-center text-sm text-muted-foreground'>
            {t('auth.signIn.noAccount')}{' '}
            <Link
              to='/sign-up'
              className='underline underline-offset-4 hover:text-primary'
            >
              {t('auth.signUp.title')}
            </Link>
          </p>
        )}
      </form>
    </Form>
  )
}
