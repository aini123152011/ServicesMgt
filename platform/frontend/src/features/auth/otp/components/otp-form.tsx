import { useState } from 'react'
import { z } from 'zod'
import { AxiosError } from 'axios'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate } from '@tanstack/react-router'
import { Loader2, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { resendVerification, verifyEmail } from '@/api/auth'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from '@/components/ui/form'
import {
  InputOTP,
  InputOTPGroup,
  InputOTPSeparator,
  InputOTPSlot,
} from '@/components/ui/input-otp'
import { LocalizedFormMessage } from '@/components/localized-form-message'
import { CaptchaField } from '../../components/captcha-field'
import { useCaptcha } from '../../hooks/use-captcha'

const formSchema = z.object({
  otp: z.string().min(6, 'auth.otp.codeInvalid').max(6, 'auth.otp.codeInvalid'),
})

interface OtpFormProps extends React.HTMLAttributes<HTMLFormElement> {
  /** 待验证的邮箱，由注册页通过路由 search 传入 */
  email: string
}

/**
 * 邮箱验证码表单。
 *
 * 提交成功只代表「邮箱已验证」，不自动登录：让用户自己回登录页再输一次凭证，
 * 避免在验证码页上隐式建立会话（也少一条容易出错的登录路径）。
 */
export function OtpForm({ email, className, ...props }: OtpFormProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [isLoading, setIsLoading] = useState(false)
  const [isResending, setIsResending] = useState(false)
  const [resendOpen, setResendOpen] = useState(false)
  const [captchaAnswer, setCaptchaAnswer] = useState('')
  const [captchaRefreshToken, setCaptchaRefreshToken] = useState(0)
  const captcha = useCaptcha('resend', captchaRefreshToken)

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: { otp: '' },
  })

  // eslint-disable-next-line react-hooks/incompatible-library
  const otp = form.watch('otp')

  function resolveErrorMessage(error: unknown, fallbackKey: string): string {
    if (error instanceof AxiosError) {
      const detail = error.response?.data?.detail
      if (typeof detail === 'string' && detail.length > 0) {
        return detail
      }
    }
    return t(fallbackKey)
  }

  /** 换一张并清空输入：票据一次性，失败后不换图用户会一直卡在「验证码错误」 */
  function refreshCaptcha() {
    setCaptchaRefreshToken((token) => token + 1)
    setCaptchaAnswer('')
  }

  function onSubmit(data: z.infer<typeof formSchema>) {
    setIsLoading(true)
    toast.promise(verifyEmail(email, data.otp), {
      loading: t('auth.otp.loading'),
      success: () => {
        setIsLoading(false)
        navigate({ to: '/sign-in', replace: true })
        return t('auth.otp.success')
      },
      error: (error) => {
        setIsLoading(false)
        // 验证码一次性/有尝试次数上限：失败后清空重填更省事
        form.reset()
        return resolveErrorMessage(error, 'auth.otp.failed')
      },
    })
  }

  function onResend() {
    setIsResending(true)
    toast.promise(resendVerification(email, captcha.captchaId, captchaAnswer), {
      loading: t('auth.otp.resending'),
      success: (result) => {
        setIsResending(false)
        setResendOpen(false)
        setCaptchaRefreshToken((token) => token + 1)
        return result.message || t('auth.otp.resendSent')
      },
      error: (error) => {
        setIsResending(false)
        setCaptchaRefreshToken((token) => token + 1)
        return resolveErrorMessage(error, 'auth.otp.failed')
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
          name='otp'
          render={({ field }) => (
            <FormItem>
              <FormLabel className='sr-only'>
                {t('auth.otp.codeLabel')}
              </FormLabel>
              <FormControl>
                <InputOTP
                  maxLength={6}
                  inputMode='numeric'
                  {...field}
                  containerClassName='justify-between sm:[&>[data-slot="input-otp-group"]>div]:w-12'
                >
                  <InputOTPGroup>
                    <InputOTPSlot index={0} />
                    <InputOTPSlot index={1} />
                  </InputOTPGroup>
                  <InputOTPSeparator />
                  <InputOTPGroup>
                    <InputOTPSlot index={2} />
                    <InputOTPSlot index={3} />
                  </InputOTPGroup>
                  <InputOTPSeparator />
                  <InputOTPGroup>
                    <InputOTPSlot index={4} />
                    <InputOTPSlot index={5} />
                  </InputOTPGroup>
                </InputOTP>
              </FormControl>
              <LocalizedFormMessage />
            </FormItem>
          )}
        />
        <Button className='mt-2' disabled={otp.length < 6 || isLoading}>
          {isLoading ? <Loader2 className='animate-spin' /> : <ShieldCheck />}
          {t('auth.otp.submit')}
        </Button>

        {/* 重发入口做成展开式：重发同样要过图片验证码，常驻显示会让页面显得很重 */}
        {resendOpen ? (
          <div className='grid gap-2 rounded-md border p-3'>
            <CaptchaField
              image={captcha.image}
              isLoading={captcha.isLoading}
              isError={captcha.isError}
              answer={captchaAnswer}
              onAnswerChange={setCaptchaAnswer}
              onRefresh={refreshCaptcha}
            />
            <div className='flex items-center gap-2'>
              <Button
                type='button'
                variant='secondary'
                disabled={isResending || !captchaAnswer}
                onClick={onResend}
              >
                {isResending && <Loader2 className='animate-spin' />}
                {t('auth.otp.resendConfirm')}
              </Button>
              <Button
                type='button'
                variant='ghost'
                onClick={() => setResendOpen(false)}
              >
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        ) : (
          <Button
            type='button'
            variant='link'
            className='text-sm'
            onClick={() => setResendOpen(true)}
          >
            {t('auth.otp.resendAction')}
          </Button>
        )}
      </form>
    </Form>
  )
}
