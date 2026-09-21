import { Check, Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  DEFAULT_LANGUAGE,
  isLanguage,
  setLanguage,
  type Language,
} from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

/**
 * 语言选项：**每种语言用它自己的文字显示**（中文 / English），不跟随当前界面语言。
 * 这是语言选择器的通行做法：界面已经是用户看不懂的语言时，用户仍能认出自己那一项；
 * 若按当前语言翻译（英文界面下显示 Chinese/English），反而让人找不到母语入口。
 */
const LANGUAGE_OPTIONS: { value: Language; label: string }[] = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
]

/**
 * 顶栏语言切换（放在主题切换旁）。
 * 不额外建 Provider：i18next 自己广播 languageChanged，useTranslation 订阅后即重渲染，
 * 语言状态只有一个事实来源（i18next 实例 + localStorage 持久化）。
 */
export function LanguageSwitch() {
  const { t, i18n } = useTranslation()
  const current = isLanguage(i18n.resolvedLanguage)
    ? i18n.resolvedLanguage
    : DEFAULT_LANGUAGE

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant='ghost' size='icon' className='scale-95 rounded-full'>
          <Languages className='size-[1.2rem]' />
          <span className='sr-only'>{t('common.language')}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end'>
        {LANGUAGE_OPTIONS.map((option) => (
          <DropdownMenuItem
            key={option.value}
            onClick={() => void setLanguage(option.value)}
          >
            {option.label}
            <Check
              size={14}
              className={cn('ms-auto', current !== option.value && 'hidden')}
            />
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
