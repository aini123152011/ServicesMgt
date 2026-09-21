import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { ContentSection } from '@/features/settings/components/content-section'
import { UpdatePanel } from '@/features/settings/components/update-panel'

// eslint-disable-next-line react-refresh/only-export-components
function SettingsUpdates() {
  const { t } = useTranslation()
  return (
    <ContentSection
      title={t('settings.updates.title')}
      description={t('settings.updates.description')}
    >
      <UpdatePanel />
    </ContentSection>
  )
}

export const Route = createFileRoute('/_authenticated/settings/updates')({
  component: SettingsUpdates,
})
