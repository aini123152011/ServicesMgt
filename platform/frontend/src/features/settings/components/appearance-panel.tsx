import { useTranslation } from 'react-i18next'
import {
  DEFAULT_LANGUAGE,
  LANGUAGE_OPTIONS,
  isLanguage,
  setLanguage,
} from '@/lib/i18n'
import { Button } from '@/components/ui/button'
import {
  LayoutConfig,
  SectionTitle,
  SidebarConfig,
  ThemeConfig,
} from '@/components/config-drawer'

/**
 * 外观设置：主题、侧边栏与布局直接复用顶栏设置抽屉的分节组件，
 * 语言用同一份 LANGUAGE_OPTIONS 与 setLanguage——两处入口共享同一套状态与逻辑，
 * 不在这里另写一份切换实现（否则顶栏与设置页会各自维护一份偏好）。
 */
export function AppearancePanel() {
  return (
    <div className='flex flex-col gap-8'>
      <ThemeConfig />
      <SidebarConfig />
      <LayoutConfig />
      <LanguageConfig />
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
