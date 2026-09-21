import { useState, useEffect, useRef } from 'react'
import { type Table } from '@tanstack/react-table'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'

type DataTableBulkActionsProps<TData> = {
  table: Table<TData>
  entityName: string
  children: React.ReactNode
}

/**
 * 表格选中行后浮出的批量操作工具栏。
 *
 * @template TData 表格数据类型
 * @param props.table react-table 实例
 * @param props.entityName 被操作实体的显示名（由调用方本地化后传入，用于屏幕阅读器播报）
 * @param props.children 工具栏里的操作按钮
 */
export function DataTableBulkActions<TData>({
  table,
  entityName,
  children,
}: DataTableBulkActionsProps<TData>): React.ReactNode | null {
  const { t } = useTranslation()
  const selectedRows = table.getFilteredSelectedRowModel().rows
  const selectedCount = selectedRows.length
  const toolbarRef = useRef<HTMLDivElement>(null)
  const [announcement, setAnnouncement] = useState('')

  // 选中行数变化时向屏幕阅读器播报；t 进依赖是因为切语言后播报文案也要跟着变
  useEffect(() => {
    if (selectedCount > 0) {
      const message = t('ui.table.bulkAnnouncement', {
        count: selectedCount,
        entity: entityName,
      })

      // 用 queueMicrotask 推迟状态更新，避免 effect 内同步 setState 造成级联渲染
      queueMicrotask(() => {
        setAnnouncement(message)
      })

      // 播报完清空，避免下一次选中时朗读旧内容
      const timer = setTimeout(() => setAnnouncement(''), 3000)
      return () => clearTimeout(timer)
    }
  }, [selectedCount, entityName, t])

  const handleClearSelection = () => {
    table.resetRowSelection()
  }

  const handleKeyDown = (event: React.KeyboardEvent) => {
    const buttons = toolbarRef.current?.querySelectorAll('button')
    if (!buttons) return

    const currentIndex = Array.from(buttons).findIndex(
      (button) => button === document.activeElement
    )

    switch (event.key) {
      case 'ArrowRight': {
        event.preventDefault()
        const nextIndex = (currentIndex + 1) % buttons.length
        buttons[nextIndex]?.focus()
        break
      }
      case 'ArrowLeft': {
        event.preventDefault()
        const prevIndex =
          currentIndex === 0 ? buttons.length - 1 : currentIndex - 1
        buttons[prevIndex]?.focus()
        break
      }
      case 'Home':
        event.preventDefault()
        buttons[0]?.focus()
        break
      case 'End':
        event.preventDefault()
        buttons[buttons.length - 1]?.focus()
        break
      case 'Escape': {
        // 判断 Esc 是不是发给下拉菜单的：下拉菜单的状态查不到——Radix 会在本处理器之前先关掉它，
        // 所以只能看事件目标/当前焦点元素是不是下拉的触发器或内容
        const target = event.target as HTMLElement
        const activeElement = document.activeElement as HTMLElement

        // 事件目标或当前焦点元素是下拉触发器
        const isFromDropdownTrigger =
          target?.getAttribute('data-slot') === 'dropdown-menu-trigger' ||
          activeElement?.getAttribute('data-slot') ===
            'dropdown-menu-trigger' ||
          target?.closest('[data-slot="dropdown-menu-trigger"]') ||
          activeElement?.closest('[data-slot="dropdown-menu-trigger"]')

        // 当前焦点元素在下拉内容里（内容被 portal 到 body 之外）
        const isFromDropdownContent =
          activeElement?.closest('[data-slot="dropdown-menu-content"]') ||
          target?.closest('[data-slot="dropdown-menu-content"]')

        if (isFromDropdownTrigger || isFromDropdownContent) {
          // 这个 Esc 是关下拉菜单的，不要清空选中
          return
        }

        // 这个 Esc 是发给工具栏的：清空选中
        event.preventDefault()
        handleClearSelection()
        break
      }
    }
  }

  if (selectedCount === 0) {
    return null
  }

  return (
    <>
      {/* Live region for screen reader announcements */}
      <div
        aria-live='polite'
        aria-atomic='true'
        className='sr-only'
        role='status'
      >
        {announcement}
      </div>

      <div
        ref={toolbarRef}
        role='toolbar'
        aria-label={t('ui.table.bulkActionsLabel', {
          count: selectedCount,
          entity: entityName,
        })}
        aria-describedby='bulk-actions-description'
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className={cn(
          'fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-xl',
          'transition-all delay-100 duration-300 ease-out hover:scale-105',
          'focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none'
        )}
      >
        <div
          className={cn(
            'p-2 shadow-xl',
            'rounded-xl border',
            'bg-background/95 backdrop-blur-lg supports-backdrop-filter:bg-background/60',
            'flex items-center gap-x-2'
          )}
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant='outline'
                size='icon'
                onClick={handleClearSelection}
                className='size-6 rounded-full'
                aria-label={t('ui.table.clearSelection')}
                title={t('ui.table.clearSelectionHint')}
              >
                <X />
                <span className='sr-only'>{t('ui.table.clearSelection')}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>{t('ui.table.clearSelectionHint')}</p>
            </TooltipContent>
          </Tooltip>

          <Separator
            className='h-5'
            orientation='vertical'
            aria-hidden='true'
          />

          <div
            className='flex items-center gap-x-1 text-sm'
            id='bulk-actions-description'
          >
            {/*
              数量与单位在各语言里语序不同（en「3 selected」/ zh「已选 3 项」），
              所以整句由一条带参文案渲染，不在这里拼「数字 + 名词」。
            */}
            <Badge variant='default' className='min-w-8 rounded-lg'>
              {t('ui.table.selectedCount', { count: selectedCount })}
            </Badge>
          </div>

          <Separator
            className='h-5'
            orientation='vertical'
            aria-hidden='true'
          />

          {children}
        </div>
      </div>
    </>
  )
}
