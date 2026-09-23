import { AlertTriangle, Info, TriangleAlert } from 'lucide-react'
import { type HostNetworkCheck } from '@/api/system'

/**
 * 校验级别的展示映射：图标 + 文字色。
 *
 * 抽成共享模块而不是在两个组件里各写一份（服务详情页的告警条与关于页的网口面板都要用），
 * 避免两处配色/图标日后各改各的。
 */
export const LEVEL_ICONS = {
  error: TriangleAlert,
  warn: AlertTriangle,
  info: Info,
} as const

/** 按级别取文字色；`ok` 用绿色对勾，没有图标。 */
export function levelTextClass(level: HostNetworkCheck['level']): string {
  if (level === 'error') return 'text-destructive'
  if (level === 'warn') return 'text-amber-600'
  return 'text-muted-foreground'
}

/** 取最高级别：决定整块告警的配色（error > warn > info）。 */
export function topLevel(
  checks: HostNetworkCheck[]
): HostNetworkCheck['level'] {
  if (checks.some((check) => check.level === 'error')) return 'error'
  if (checks.some((check) => check.level === 'warn')) return 'warn'
  return 'info'
}
