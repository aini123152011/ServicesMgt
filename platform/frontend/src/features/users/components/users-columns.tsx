import { type ColumnDef } from '@tanstack/react-table'
import { type TFunction } from 'i18next'
import { type UserPublic } from '@/api/auth'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { DataTableColumnHeader } from '@/components/data-table'
import { LongText } from '@/components/long-text'
import { roleMeta, userEmailVerifiedMeta, userStatusMeta } from '../data/data'
import { DataTableRowActions } from './data-table-row-actions'

/**
 * 列定义工厂：列标题与徽章文案要随语言切换，而列定义是模块级数据、拿不到最新的 t，
 * 因此把 t 作为参数传入（组件内 useMemo 按 t 重建）。
 */
export function buildUsersColumns(t: TFunction): ColumnDef<UserPublic>[] {
  return [
    {
      accessorKey: 'email',
      header: ({ column }) => (
        <DataTableColumnHeader column={column} title={t('users.field.email')} />
      ),
      cell: ({ row }) => (
        <LongText className='max-w-48 ps-3'>{row.getValue('email')}</LongText>
      ),
      meta: {
        className: cn(
          'inset-s-6 ps-0.5 max-md:sticky @4xl/content:table-cell',
          'drop-shadow-[0_1px_2px_rgb(0_0_0_/_0.1)] @4xl/content:drop-shadow-none dark:drop-shadow-[0_1px_2px_rgb(255_255_255_/_0.1)]'
        ),
      },
      enableHiding: false,
    },
    {
      id: 'fullName',
      accessorFn: (row) => row.full_name ?? '',
      header: ({ column }) => (
        <DataTableColumnHeader
          column={column}
          title={t('users.field.fullName')}
        />
      ),
      cell: ({ row }) => (
        <LongText className='max-w-36'>
          {row.original.full_name || '—'}
        </LongText>
      ),
      meta: { className: 'w-36' },
    },
    {
      id: 'status',
      // 归一化为 'active' | 'inactive'，与路由 search 的状态筛选枚举对应
      accessorFn: (row) => (row.is_active ? 'active' : 'inactive'),
      header: ({ column }) => (
        <DataTableColumnHeader
          column={column}
          title={t('users.field.status')}
        />
      ),
      cell: ({ row }) => {
        const meta = userStatusMeta.get(row.original.is_active)
        if (!meta) return null
        return (
          <div className='flex space-x-2'>
            <Badge variant='outline' className={cn(meta.className)}>
              {t(meta.labelKey)}
            </Badge>
          </div>
        )
      },
      filterFn: (row, id, value) => {
        return value.includes(row.getValue(id))
      },
      enableSorting: false,
      enableHiding: false,
    },
    {
      id: 'emailVerified',
      // 归一化为 'verified' | 'unverified'，与路由 search 的筛选枚举对应
      accessorFn: (row) => (row.email_verified_at ? 'verified' : 'unverified'),
      header: ({ column }) => (
        <DataTableColumnHeader
          column={column}
          title={t('users.field.emailVerified')}
        />
      ),
      cell: ({ row }) => {
        const meta = userEmailVerifiedMeta.get(
          row.original.email_verified_at !== null
        )
        if (!meta) return null
        return (
          <Badge variant='outline' className={cn(meta.className)}>
            {t(meta.labelKey)}
          </Badge>
        )
      },
      filterFn: (row, id, value) => value.includes(row.getValue(id)),
      enableSorting: false,
    },
    {
      id: 'roles',
      accessorFn: (row) => row.roles,
      header: ({ column }) => (
        <DataTableColumnHeader column={column} title={t('users.field.roles')} />
      ),
      cell: ({ row }) => (
        <div className='flex max-w-56 flex-wrap gap-1'>
          {row.original.roles.map((role) => {
            const meta = roleMeta(role)
            return (
              <Badge
                key={role}
                variant='outline'
                className={cn(meta.className)}
              >
                <meta.icon size={12} />
                {meta.labelKey ? t(meta.labelKey) : meta.label}
              </Badge>
            )
          })}
        </div>
      ),
      // 一个用户可有多角色：任一命中即通过筛选
      filterFn: (row, id, value) => {
        const roles = row.getValue<string[]>(id) ?? []
        return roles.some((role) => value.includes(role))
      },
      enableSorting: false,
      enableHiding: false,
    },
    {
      id: 'actions',
      cell: DataTableRowActions,
    },
  ]
}
