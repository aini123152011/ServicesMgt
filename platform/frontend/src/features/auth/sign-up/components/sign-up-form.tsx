import { useState } from 'react'
import { z } from 'zod'
import { AxiosError } from 'axios'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate } from '@tanstack/react-router'
import { Loader2, UserPlus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { register } from '@/api/auth'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormDescription,
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
const formSchema = z
  .object({
    email: z.email({
      error: (iss) =>
        iss.input === ''
          ? 'auth.validation.emailRequired'
          : 'auth.validation.emailInvalid',
    }),
    fullName: z.string().optional(),
    password: z
      .string()
      .min(1, 'auth.validation.passwordRequired')
      .min(8, 'auth.validation.passwordMinRegister'),
    confirmPassword: z.string().min(1, 'auth.validation.confirmRequired'),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: 'auth.validation.passwordMismatch',
    path: ['confirmPassword'],
  })

type SignUpFormProps = React.HTMLAttributes<HTMLFormElement>

/**
 * 注册表单：提交成功后**账号仍是未验证状态**，必须用邮件里的 6 位码完成验证，
 * 因此这里跳转到验证码页而不是直接登录。
 */
export function SignUpForm({ className, ...props }: SignUpFormProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [isLoading, setIsLoading] = useState(false)
  const [captchaAnswer, setCaptchaAnswer] = useState('')
  const [captchaRefreshToken, setCaptchaRefreshToken] = useState(0)
  const captcha = useCaptcha('register', captchaRefreshToken)

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      email: '',
      fullName: '',
      password: '',
      confirmPassword: '',
    },
  })

  /** 后端失败响应统一带 { detail }，优先展示给用户 */
  function resolveErrorMessage(error: unknown): string {
    if (error instanceof AxiosError) {
      const detail = error.response?.data?.detail
      if (typeof detail === 'string' && detail.length > 0) {
        return detail
      }
    }
    return t('auth.signUp.failed')
  }

  /** 换一张并清空输入：票据一次性，失败后不换图用户会一直卡在「验证码错误」 */
  function refreshCaptcha() {
    setCaptchaRefreshToken((token) => token + 1)
    setCaptchaAnswer('')
  }

  function onSubmit(data: z.infer<typeof formSchema>) {
    setIsLoading(true)
    const email = data.email

    const submit = async () => {
      try {
        await register({
          email,
          password: data.password,
          full_name: data.fullName?.trim() ? data.fullName.trim() : null,
          captcha_id: captcha.captchaId,
          captcha_answer: captchaAnswer,
        })
      } catch (error) {
        refreshCaptcha()
        throw error
      }
      return email
    }

    toast.promise(submit(), {
      loading: t('auth.signUp.loading'),
      success: (registeredEmail) => {
        setIsLoading(false)
        navigate({
          to: '/otp',
          search: { email: registeredEmail },
          replace: true,
        })
        return t('auth.signUp.success')
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
          name='fullName'
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('auth.signUp.fullNameLabel')}</FormLabel>
              <FormControl>
                <Input
                  placeholder={t('auth.signUp.fullNamePlaceholder')}
                  {...field}
                />
              </FormControl>
              <FormDescription>{t('auth.signUp.fullNameHint')}</FormDescription>
              <LocalizedFormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name='password'
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('auth.passwordLabel')}</FormLabel>
              <FormControl>
                <PasswordInput placeholder='********' {...field} />
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
              <FormLabel>{t('auth.signUp.confirmPasswordLabel')}</FormLabel>
              <FormControl>
                <PasswordInput placeholder='********' {...field} />
              </FormControl>
              <LocalizedFormMessage />
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
          {isLoading ? <Loader2 className='animate-spin' /> : <UserPlus />}
          {t('auth.signUp.submit')}
        </Button>
      </form>
    </Form>
  )
}
