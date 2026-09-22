import { RotateCcw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  DEFAULT_LANGUAGE,
  LANGUAGE_OPTIONS,
  isLanguage,
  setLanguage,
} from '@/lib/i18n'
import { useLayout } from '@/context/layout-provider'
import { useTheme } from '@/context/theme-provider'
import { Button } from '@/components/ui/button'
import { useSidebar } from '@/components/ui/sidebar'
import {
  LayoutConfig,
  SectionTitle,
  SidebarConfig,
  ThemeConfig,
} from './appearance-config'

/**
 * 外观设置：主题、侧边栏与布局直接复用外观配置分节，语言用同一份 LANGUAGE_OPTIONS
 * 与 setLanguage——顶栏的快捷切换与这里共享同一套状态与逻辑，不另写一份实现。
 */
export function AppearancePanel() {
  const { t } = useTranslation()
  const { setOpen } = useSidebar()
  const { resetTheme } = useTheme()
  const { resetLayout } = useLayout()

  // 重置后把侧边栏展开：布局可能刚从 offcanvas/full 回到默认，展开才看得到结果
  const handleReset = () => {
    resetTheme()
    resetLayout()
    setOpen(true)
  }

  return (
    <div className='flex flex-col gap-8'>
      <ThemeConfig />
      <SidebarConfig />
      <LayoutConfig />
      <LanguageConfig />
      <div>
        <Button variant='destructive' onClick={handleReset}>
          <RotateCcw />
          {t('settings.appearance.resetAll')}
        </Button>
      </div>
    </div>
  )
}

function LanguageConfig() {
  const { t, i18n } = useTranslation()
  const current = isLanguage(i18n.resolvedLanguage)
    ? i18n.resolvedLanguage
    : DEFAULT_LANGUAGE

  return (
    <div>
      <SectionTitle title={t('settings.appearance.language')} />
      <div className='flex flex-wrap gap-2'>
        {LANGUAGE_OPTIONS.map((option) => (
          <Button
            key={option.value}
            type='button'
            size='sm'
            variant={current === option.value ? 'default' : 'outline'}
            onClick={() => void setLanguage(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>
      <p className='mt-2 text-xs text-muted-foreground'>
        {t('settings.appearance.languageHint')}
      </p>
    </div>
  )
}
