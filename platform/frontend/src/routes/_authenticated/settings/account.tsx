import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { AccountPasswordForm } from '@/features/settings/components/account-password-form'
import { AccountProfileForm } from '@/features/settings/components/account-profile-form'
import { ContentSection } from '@/features/settings/components/content-section'

// eslint-disable-next-line react-refresh/only-export-components
function SettingsAccount() {
  const { t } = useTranslation()
  return (
    <ContentSection
      title={t('settings.account.title')}
      description={t('settings.account.description')}
    >
      <AccountProfileForm />
      <AccountPasswordForm />
    </ContentSection>
  )
}

export const Route = createFileRoute('/_authenticated/settings/account')({
  component: SettingsAccount,
})
