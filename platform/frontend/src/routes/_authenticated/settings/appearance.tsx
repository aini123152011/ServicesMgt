import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { AppearancePanel } from '@/features/settings/components/appearance-panel'
import { ContentSection } from '@/features/settings/components/content-section'

// eslint-disable-next-line react-refresh/only-export-components
function SettingsAppearance() {
  const { t } = useTranslation()
  return (
    <ContentSection
      title={t('settings.appearance.title')}
      description={t('settings.appearance.description')}
    >
      <AppearancePanel />
    </ContentSection>
  )
}

export const Route = createFileRoute('/_authenticated/settings/appearance')({
  component: SettingsAppearance,
})
