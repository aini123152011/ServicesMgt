import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { AboutPanel } from '@/features/settings/components/about-panel'
import { ContentSection } from '@/features/settings/components/content-section'

// eslint-disable-next-line react-refresh/only-export-components
function SettingsAbout() {
  const { t } = useTranslation()
  return (
    <ContentSection
      title={t('settings.about.title')}
      description={t('settings.about.description')}
    >
      <AboutPanel />
    </ContentSection>
  )
}

export const Route = createFileRoute('/_authenticated/settings/about')({
  component: SettingsAbout,
})
