import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  updateCurrentUser,
  updateCurrentUserPassword,
  type PasswordUpdatePayload,
  type UserSelfUpdatePayload,
} from '@/api/users'
import { useAuthStore } from '@/stores/auth-store'

/**
 * 修改自身资料；成功后把返回的用户写回 auth store。
 *
 * user 只在登录时写入、刷新时靠 /users/me 水合，若不同步这里，
 * 页头与侧边栏的姓名会一直停在修改前的值。
 */
export function useUpdateProfileMutation() {
  const setUser = useAuthStore((state) => state.auth.setUser)
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (payload: UserSelfUpdatePayload) => updateCurrentUser(payload),
    onSuccess: (user) => {
      setUser(user)
      toast.success(t('settings.account.profile.saved'))
    },
  })
}

/** 修改密码；后端不换发 token，当前会话继续有效，无需重新登录 */
export function useUpdatePasswordMutation() {
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (payload: PasswordUpdatePayload) =>
      updateCurrentUserPassword(payload),
    onSuccess: () => {
      toast.success(t('settings.account.password.saved'))
    },
  })
}
