import * as React from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Button } from './ui/button'

type PasswordInputProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  'type'
> & {
  ref?: React.Ref<HTMLInputElement>
  /**
   * 值为掩码占位符时提供：切到明文前先取回真实值（并写回受控值）。
   * 没有它的话「显示」出来的只是 ******** ——掩码是服务端产物，不是 CSS 遮蔽。
   */
  onReveal?: () => Promise<void>
}

export function PasswordInput({
  className,
  disabled,
  ref,
  onReveal,
  ...props
}: PasswordInputProps) {
  const [showPassword, setShowPassword] = React.useState(false)
  const [revealing, setRevealing] = React.useState(false)
  const { t } = useTranslation()

  const toggle = async () => {
    if (showPassword) {
      setShowPassword(false)
      return
    }
    if (onReveal) {
      setRevealing(true)
      try {
        await onReveal()
      } finally {
        setRevealing(false)
      }
    }
    setShowPassword(true)
  }

  return (
    <div className={cn('relative rounded-md', className)}>
      <input
        type={showPassword ? 'text' : 'password'}
        className='flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-hidden disabled:cursor-not-allowed disabled:opacity-50'
        ref={ref}
        disabled={disabled}
        {...props}
      />
      <Button
        type='button'
        size='icon'
        variant='ghost'
        disabled={disabled || revealing}
        className='absolute inset-e-1 top-1/2 h-6 w-6 -translate-y-1/2 rounded-md text-muted-foreground'
        onClick={() => void toggle()}
      >
        {showPassword ? <Eye size={18} /> : <EyeOff size={18} />}
        <span className='sr-only'>
          {showPassword ? t('common.hidePassword') : t('common.showPassword')}
        </span>
      </Button>
    </div>
  )
}
