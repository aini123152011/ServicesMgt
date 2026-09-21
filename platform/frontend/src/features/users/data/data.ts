import { Eye, ShieldCheck, Wrench, type LucideIcon } from 'lucide-react'
import { ROLE_NAMES, type RoleName } from '@/api/users'
import { type TranslationKey } from '@/lib/i18n'

/**
 * 角色/状态展示元数据。
 *
 * 文案一律存 i18n key：这些映射是模块级常量，直接存中文会让语言切换后徽章停留在旧语言，
 * 由渲染处 t() 取值。契约锁定三角色，未知角色名由 isRoleName 拦截并退回原文。
 */
const roleLabelKeys: Record<RoleName, TranslationKey> = {
  admin: 'users.role.admin',
  operator: 'users.role.operator',
  readonly: 'users.role.readonly',
}

const roleIcons: Record<RoleName, LucideIcon> = {
  admin: ShieldCheck,
  operator: Wrench,
  readonly: Eye,
}

/** 角色 Badge 样式映射，key 与后端角色名一致 */
const roleBadgeClass: Record<RoleName, string> = {
  admin: 'bg-sky-100/40 text-sky-900 dark:text-sky-100 border-sky-300',
  operator:
    'bg-violet-100/40 text-violet-900 dark:text-violet-100 border-violet-300',
  readonly: 'bg-neutral-200/50 text-neutral-800 dark:text-neutral-200',
}

function isRoleName(role: string): role is RoleName {
  return ROLE_NAMES.some((name) => name === role)
}

/** 供表单把 GET /roles 返回的字符串收窄为契约内角色名 */
export const isKnownRole = isRoleName

/** 单个角色的展示元数据；契约外的角色名退回原文与 Badge 默认样式 */
export function roleMeta(role: string): {
  labelKey?: TranslationKey
  label?: string
  icon: LucideIcon
  className?: string
} {
  if (isRoleName(role)) {
    return {
      labelKey: roleLabelKeys[role],
      icon: roleIcons[role],
      className: roleBadgeClass[role],
    }
  }
  return { label: role, icon: Eye }
}

/** 角色多选/筛选选项（对话框 Checkbox 组与 FacetedFilter 复用） */
export const roleOptions = ROLE_NAMES.map((value) => ({
  value,
  labelKey: roleLabelKeys[value],
  icon: roleIcons[value],
}))

/** is_active → 状态 Badge 文案 key 与样式 */
export const userStatusMeta = new Map<
  boolean,
  { labelKey: TranslationKey; className: string }
>([
  [
    true,
    {
      labelKey: 'users.status.active',
      className:
        'bg-teal-100/30 text-teal-900 dark:text-teal-200 border-teal-200',
    },
  ],
  [
    false,
    {
      labelKey: 'users.status.inactive',
      className:
        'bg-destructive/10 dark:bg-destructive/50 text-destructive dark:text-primary border-destructive/10',
    },
  ],
])

/** 状态筛选选项，value 与路由 search 的枚举对应 */
export const userStatusOptions: { labelKey: TranslationKey; value: string }[] =
  [
    { labelKey: 'users.status.active', value: 'active' },
    { labelKey: 'users.status.inactive', value: 'inactive' },
  ]
