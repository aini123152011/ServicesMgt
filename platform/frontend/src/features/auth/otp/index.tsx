import { Link, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { AuthLayout } from '../auth-layout'
import { OtpForm } from './components/otp-form'

/**
 * 邮箱验证码页。
 *
 * 邮箱从路由 search 传入（注册成功后跳过来）。直接访问且没带邮箱时不给空表单——
 * 没有目标邮箱的表单提交出去只会拿到一句「验证码错误」，不如直接把人引回注册页。
 */
export function Otp() {
  const { email } = useSearch({ from: '/(auth)/otp' })
  const { t } = useTranslation()

  return (
    <AuthLayout>
      <Card className='max-w-md gap-4'>
        <CardHeader>
          <CardTitle className='text-lg tracking-tight'>
            {t('auth.otp.title')}
          </CardTitle>
          <CardDescription>
            {email
              ? t('auth.otp.description', { email })
              : t('auth.otp.missingEmail')}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {email ? (
            <OtpForm email={email} />
          ) : (
            <Link
              to='/sign-up'
              className='text-sm underline underline-offset-4 hover:text-primary'
            >
              {t('auth.otp.backToSignUp')}
            </Link>
          )}
        </CardContent>
        <CardFooter>
          <p className='px-8 text-center text-sm text-muted-foreground'>
            {t('auth.otp.footerHint')}
          </p>
        </CardFooter>
      </Card>
    </AuthLayout>
  )
}
