import { useTranslation } from 'react-i18next'
import { translateMessage } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { useFormField } from '@/components/ui/form'

/**
 * FormMessage 的本地化版本。
 *
 * 校验消息在 schema 里存的是 i18n key（schema 在模块级创建，取不到最新语言），
 * 这里在渲染处翻译，语言切换后已显示的错误提示立即跟随。
 * 不直接改 components/ui/form.tsx —— 那是 shadcn 生成物，升级会被覆盖。
 */
export function LocalizedFormMessage({
  className,
  ...props
}: React.ComponentProps<'p'>) {
  const { error, formMessageId } = useFormField()
  const { t } = useTranslation()
  const body = error
    ? translateMessage(t, String(error.message ?? ''))
    : props.children

  if (!body) {
    return null
  }

  return (
    <p
      data-slot='form-message'
      id={formMessageId}
      className={cn('text-sm text-destructive', className)}
      {...props}
    >
      {body}
    </p>
  )
}
