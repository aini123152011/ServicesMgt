import { AlertCircle, Loader2, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface CaptchaFieldProps {
  /** 图片（data URL），来自 useCaptcha */
  image: string
  isLoading: boolean
  isError?: boolean
  /** 用户填写的字符 */
  answer: string
  onAnswerChange: (answer: string) => void
  /** 换一张。调用方应同时清空 answer —— 旧答案配新图必然错 */
  onRefresh: () => void
  className?: string
}

/**
 * 图片验证码字段（纯展示）。
 *
 * 只负责画图与收集输入，取图交给 `useCaptcha`：这样组件里没有 effect、没有副作用，
 * 换图/清空这类联动由表单统一控制，三处调用（登录/注册/重发）行为一致。
 */
export function CaptchaField({
  image,
  isLoading,
  isError = false,
  answer,
  onAnswerChange,
  onRefresh,
  className,
}: CaptchaFieldProps) {
  const { t } = useTranslation()

  return (
    <div className={cn('grid gap-2', className)}>
      <Label htmlFor='captcha-answer'>{t('auth.captcha.label')}</Label>
      <div className='flex items-center gap-2'>
        <Input
          id='captcha-answer'
          value={answer}
          autoComplete='off'
          spellCheck={false}
          maxLength={8}
          placeholder={t('auth.captcha.placeholder')}
          className='font-mono tracking-widest uppercase'
          onChange={(event) => onAnswerChange(event.target.value)}
        />
        {/* 图片本身可点：比只给一个小刷新按钮更好按，也是常见约定 */}
        <button
          type='button'
          onClick={onRefresh}
          title={t('auth.captcha.refresh')}
          aria-label={t('auth.captcha.refresh')}
          className='flex h-9 w-32 shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted/40'
        >
          {isLoading ? (
            <Loader2 className='size-4 animate-spin text-muted-foreground' />
          ) : image ? (
            <img src={image} alt={t('auth.captcha.label')} className='h-full' />
          ) : (
            <AlertCircle className='size-4 text-destructive' />
          )}
        </button>
        <Button
          type='button'
          variant='ghost'
          size='icon'
          disabled={isLoading}
          onClick={onRefresh}
          title={t('auth.captcha.refresh')}
        >
          <RefreshCw className={cn(isLoading && 'animate-spin')} />
          <span className='sr-only'>{t('auth.captcha.refresh')}</span>
        </Button>
      </div>
      {isError && (
        <p className='text-sm text-destructive'>
          {t('auth.captcha.loadFailed')}
        </p>
      )}
    </div>
  )
}
