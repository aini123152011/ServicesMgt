import { useEffect, useMemo, useState } from 'react'
import {
  type Table as TanstackTable,
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { type UserPublic } from '@/api/auth'
import { cn } from '@/lib/utils'
import { type NavigateFn, useTableUrlState } from '@/hooks/use-table-url-state'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { DataTablePagination, DataTableToolbar } from '@/components/data-table'
import { roleOptions, userStatusOptions } from '../data/data'
import { buildUsersColumns } from './users-columns'

type DataTableProps = {
  data: UserPublic[]
  search: Record<string, unknown>
  navigate: NavigateFn
}

// 角色列一行多值，内置 faceted 统计不适用：逐角色累计；其余列沿用默认实现
function facetedUniqueValues(
  table: TanstackTable<UserPublic>,
  columnId: string
): () => Map<string, number> {
  if (columnId === 'roles') {
    return () => {
      const counts = new Map<string, number>()
      for (const row of table.getCoreRowModel().flatRows) {
        for (const role of row.getValue<string[]>(columnId) ?? []) {
          counts.set(role, (counts.get(role) ?? 0) + 1)
        }
      }
      return counts
    }
  }
  return getFacetedUniqueValues<UserPublic>()(table, columnId)
}

export function UsersTable({ data, search, navigate }: DataTableProps) {
  const { t } = useTranslation()
  // Local UI-only states
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const [sorting, setSorting] = useState<SortingState>([])
  // 列定义含文案：t 变化（切换语言）时重建，列标题与徽章才会跟着变
  const columns = useMemo(() => buildUsersColumns(t), [t])

  // Synced with URL states (keys/defaults mirror users route search schema)
  const {
    columnFilters,
    onColumnFiltersChange,
    pagination,
    onPaginationChange,
    ensurePageInRange,
  } = useTableUrlState({
    search,
    navigate,
    pagination: { defaultPage: 1, defaultPageSize: 10 },
    globalFilter: { enabled: false },
    columnFilters: [
      // email per-column text filter
      { columnId: 'email', searchKey: 'email', type: 'string' },
      { columnId: 'status', searchKey: 'status', type: 'array' },
      { columnId: 'roles', searchKey: 'role', type: 'array' },
    ],
  })

  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns,
    state: {
      sorting,
      pagination,
      columnFilters,
      columnVisibility,
    },
    onPaginationChange,
    onColumnFiltersChange,
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getPaginationRowModel: getPaginationRowModel(),
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: facetedUniqueValues,
  })

  useEffect(() => {
    ensurePageInRange(table.getPageCount())
  }, [table, ensurePageInRange])

  return (
    <div
      className={cn(
        'max-sm:has-[div[role="toolbar"]]:mb-16', // Add margin bottom to the table on mobile when the toolbar is visible
        'flex flex-1 flex-col gap-4'
      )}
    >
      <DataTableToolbar
        table={table}
        searchPlaceholder={t('users.filterEmailPlaceholder')}
        searchKey='email'
        filters={[
          {
            columnId: 'status',
            title: t('users.field.status'),
            // 通用表格组件只认 label，选项里存的是 key，在这里翻译
            options: userStatusOptions.map((option) => ({
              value: option.value,
              label: t(option.labelKey),
            })),
          },
          {
            columnId: 'roles',
            title: t('users.field.roles'),
            options: roleOptions.map((role) => ({
              value: role.value,
              label: t(role.labelKey),
              icon: role.icon,
            })),
          },
        ]}
      />
      <div className='overflow-hidden rounded-md border'>
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className='group/row'>
                {headerGroup.headers.map((header) => {
                  return (
                    <TableHead
                      key={header.id}
                      colSpan={header.colSpan}
                      className={cn(
                        'bg-background group-hover/row:bg-muted group-data-[state=selected]/row:bg-muted',
                        header.column.columnDef.meta?.className,
                        header.column.columnDef.meta?.thClassName
                      )}
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(
                            header.column.columnDef.header,
                            header.getContext()
                          )}
                    </TableHead>
                  )
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={row.getIsSelected() && 'selected'}
                  className='group/row'
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={cn(
                        'bg-background group-hover/row:bg-muted group-data-[state=selected]/row:bg-muted',
                        cell.column.columnDef.meta?.className,
                        cell.column.columnDef.meta?.tdClassName
                      )}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className='h-24 text-center'
                >
                  {t('users.empty')}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <DataTablePagination table={table} className='mt-auto' />
    </div>
  )
}
