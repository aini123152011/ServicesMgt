import { type Table } from '@tanstack/react-table'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import i18n, { DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY } from '@/lib/i18n'
import { DataTablePagination } from './pagination'

/**
 * 分页区是模板遗留英文最先暴露的地方（用户管理页表格曾显示 `Rows per page`），
 * 这里用最小表格替身直接断言中英两套渲染文案，防止再退回硬编码英文。
 *
 * 断言的是「可见文案 + 无障碍文案」两类：分页按钮只有图标，
 * 用户感知到的文字全在 sr-only / aria 里，正是最容易漏翻的部分。
 */
function createPaginationTable({
  pageIndex = 0,
  pageSize = 10,
  pageCount = 3,
}: {
  pageIndex?: number
  pageSize?: number
  pageCount?: number
} = {}): Table<Record<string, unknown>> {
  const state = { pagination: { pageIndex, pageSize } }
  return {
    getState: () => state,
    getPageCount: () => pageCount,
    getCanPreviousPage: () => pageIndex > 0,
    getCanNextPage: () => pageIndex < pageCount - 1,
    setPageIndex: vi.fn(),
    setPageSize: vi.fn(),
    previousPage: vi.fn(),
    nextPage: vi.fn(),
  } as unknown as Table<Record<string, unknown>>
}

beforeEach(async () => {
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

afterEach(async () => {
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

describe('DataTablePagination 文案', () => {
  it('默认中文：可见文案与按钮无障碍文案都是中文', async () => {
    const screen = await render(
      <DataTablePagination table={createPaginationTable()} />
    )

    await expect.element(screen.getByText('每页行数')).toBeInTheDocument()
    // 「第 n / m 页」在窄屏与宽屏各有一处（CSS 控制显隐，DOM 里都在）
    await expect
      .element(screen.getByText('第 1 / 3 页').first())
      .toBeInTheDocument()

    await expect
      .element(screen.getByRole('button', { name: '首页' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: '上一页' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: '下一页' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: '末页' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: '转到第 2 页' }))
      .toBeInTheDocument()
  })

  it('英文：切换语言后同一组件渲染英文文案', async () => {
    await i18n.changeLanguage('en')

    const screen = await render(
      <DataTablePagination table={createPaginationTable()} />
    )

    await expect.element(screen.getByText('Rows per page')).toBeInTheDocument()
    await expect
      .element(screen.getByText('Page 1 of 3').first())
      .toBeInTheDocument()

    await expect
      .element(screen.getByRole('button', { name: 'Go to first page' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: 'Go to previous page' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: 'Go to next page' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: 'Go to last page' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: 'Go to page 2' }))
      .toBeInTheDocument()
  })

  it('切换语言后已挂载的分页区立即跟着变（无需重新挂载）', async () => {
    const screen = await render(
      <DataTablePagination table={createPaginationTable()} />
    )

    await expect.element(screen.getByText('每页行数')).toBeInTheDocument()

    await i18n.changeLanguage('en')

    await expect.element(screen.getByText('Rows per page')).toBeInTheDocument()
    await expect.element(screen.getByText('每页行数')).not.toBeInTheDocument()
  })
})
