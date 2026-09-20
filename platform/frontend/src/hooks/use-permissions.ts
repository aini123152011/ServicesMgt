import { useAuthStore } from '@/stores/auth-store'

/**
 * 基于当前登录用户的角色权限判定。
 * is_superuser 视为等价 admin（其 roles 里可能没有 'admin'）；
 * readonly 之外的角色才可改配置/执行生命周期操作，用户管理仅 admin。
 */
export function usePermissions() {
  const user = useAuthStore((state) => state.auth.user)
  const roles = user?.roles ?? []
  const isAdmin = roles.includes('admin') || (user?.is_superuser ?? false)
  const isOperator = isAdmin || roles.includes('operator')
  return { isAdmin, isOperator, roles }
}
