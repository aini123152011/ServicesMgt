import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  type AccessRuleKind,
  type AccessRulePayload,
  createAccessRule,
  deleteAccessRule,
  getAccessControlSettings,
  listAccessRules,
  updateAccessControlSettings,
} from '@/api/access-control'

/** 某类准入规则（kind 进 queryKey：邮箱规则与 IP 规则各自缓存） */
export function useAccessRulesQuery(kind: AccessRuleKind) {
  return useQuery({
    queryKey: ['access-control', 'rules', kind],
    queryFn: () => listAccessRules(kind),
  })
}

/** 访问控制开关（注册开关 + 邮件是否已配置） */
export function useAccessControlSettingsQuery() {
  return useQuery({
    queryKey: ['access-control', 'settings'],
    queryFn: getAccessControlSettings,
  })
}

export function useCreateAccessRuleMutation(kind: AccessRuleKind) {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (payload: AccessRulePayload) => createAccessRule(payload),
    onSuccess: () => {
      toast.success(t('settings.accessControl.added'))
      queryClient.invalidateQueries({
        queryKey: ['access-control', 'rules', kind],
      })
    },
    // 失败（含防自锁拒绝）经全局 mutation onError 展示后端 detail，不在此重复处理
  })
}

export function useDeleteAccessRuleMutation(kind: AccessRuleKind) {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (id: string) => deleteAccessRule(id),
    onSuccess: () => {
      toast.success(t('settings.accessControl.deleted'))
      queryClient.invalidateQueries({
        queryKey: ['access-control', 'rules', kind],
      })
    },
  })
}

export function useUpdateAccessControlSettingsMutation() {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (payload: { registration_enabled: boolean }) =>
      updateAccessControlSettings(payload),
    onSuccess: () => {
      toast.success(t('settings.accessControl.registrationSaved'))
      queryClient.invalidateQueries({
        queryKey: ['access-control', 'settings'],
      })
      // 登录页的注册入口依赖 availability，开关一变要跟着失效
      queryClient.invalidateQueries({ queryKey: ['registration-available'] })
    },
  })
}
