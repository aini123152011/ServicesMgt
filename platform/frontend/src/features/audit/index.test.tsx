import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type AuditLogEntry } from '@/api/audit'
import { type ServiceSummary } from '@/api/services'
import { AuditLogs } from './index'

const mocks = vi.hoisted(() => ({
  useAuditLogsPageQuery: vi.fn(),
  useAuditActionsQuery: vi.fn(),
  useServicesQuery: vi.fn(),
  navigate: vi.fn(),
  refetch: vi.fn(),
  search: {} as Record<string, unknown>,
}))

vi.mock('./hooks/use-audit', () => ({
  useAuditLogsPageQuery: mocks.useAuditLogsPageQuery,
  useAuditActionsQuery: mocks.useAuditActionsQuery,
}))

vi.mock('@/features/services/hooks/use-services', () => ({
  useServicesQuery: mocks.useServicesQuery,
}))

// 顶栏是公共壳子，与本页的数据断言无关：整块替换，免得为它拉起 SidebarProvider 与命令面板
vi.mock('@/components/layout/header', () => ({
  Header: ({ children }: { children?: React.ReactNode }) => (
    <header>{children}</header>
  ),
}))
vi.mock('@/components/search', () => ({ Search: () => null }))
// 配置抽屉依赖 SidebarProvider，同样与页面断言无关
vi.mock('@/components/layout/header-actions', () => ({
  HeaderActions: () => null,
}))
vi.mock('@/components/language-switch', () => ({ LanguageSwitch: () => null }))
vi.mock('@/components/theme-switch', () => ({ ThemeSwitch: () => null }))
vi.mock('@/components/profile-dropdown', () => ({
  ProfileDropdown: () => null,
}))

// 页面用 getRouteApi 读查询条件、写回 URL：换成假 route 对象，断言 navigate 的入参
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    getRouteApi: () => ({
      useSearch: () => mocks.search,
      useNavigate: () => mocks.navigate,
    }),
  }
})

const ENTRIES: AuditLogEntry[] = [
  {
    id: 'log-1',
    user_email: 'admin@bmcplatform.cn',
    action: 'config.update',
    service_name: 'chrony',
    detail: 'applied=true',
    created_at: '2026-09-21T08:00:00Z',
  },
  {
    id: 'log-2',
    // 后端可能新增前端未收录的动作：必须按原文展示，不能渲染成空白
    user_email: null,
    action: 'brand.new.action',
    service_name: null,
    detail: null,
    created_at: null,
  },
]

const SERVICES: ServiceSummary[] = [
  {
    name: 'chrony',
    display_name: '时间同步服务',
    category: 'time',
    description: null,
    container_name: 'fx-chrony',
    ports: [{ port: 123, protocol: 'udp', description: null }],
    reload_mode: 'restart',
  },
]

/** 表格某一行的全部单元格文本 */
function rowTexts(screen: RenderResult): string[][] {
  return Array.from(screen.container.querySelectorAll('tbody tr')).map((row) =>
    Array.from(row.querySelectorAll('td')).map((cell) => cell.textContent ?? '')
  )
}

/** 取出 navigate 收到的 search 更新函数，并按给定旧条件求值 */
function navigateSearchUpdater(callIndex = 0) {
  const updater = mocks.navigate.mock.calls[callIndex][0].search as (
    prev: Record<string, unknown>
  ) => Record<string, unknown>
  return updater
}

async function renderPage() {
  return await render(<AuditLogs />)
}

describe('AuditLogs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.search = {}
    mocks.useAuditActionsQuery.mockReturnValue({ data: ['config.update'] })
    mocks.useServicesQuery.mockReturnValue({ data: SERVICES })
    mocks.useAuditLogsPageQuery.mockReturnValue({
      data: { data: ENTRIES, count: 2 },
      isPending: false,
      isError: false,
      isFetching: false,
      refetch: mocks.refetch,
    })
  })

  it('渲染记录、把动作名翻成文案，未收录的动作回退为原文', async () => {
    const screen = await renderPage()

    const rows = rowTexts(screen)
    expect(rows).toHaveLength(2)
    // 第一行：动作被翻成中文文案，其余字段按原值展示
    expect(rows[0][2]).toBe('修改配置')
    expect(rows[0][3]).toBe('chrony')
    expect(rows[0][4]).toBe('applied=true')
    // 第二行：动作不在字典里按原文展示；缺失字段用占位符而不是空白
    expect(rows[1][2]).toBe('brand.new.action')
    expect(rows[1][1]).toBe('—')
  })

  it('展示过滤后的总条数，而不是当前页条数', async () => {
    mocks.useAuditLogsPageQuery.mockReturnValue({
      data: { data: ENTRIES, count: 42 },
      isPending: false,
      isError: false,
      isFetching: false,
      refetch: mocks.refetch,
    })

    const screen = await renderPage()
    await expect.element(screen.getByText('共 42 条记录')).toBeVisible()
  })

  it('关键字回车才提交，输入过程中不发请求', async () => {
    const screen = await renderPage()

    await userEvent.fill(screen.getByLabelText('按关键字筛选'), 'ops@bmc')
    expect(mocks.navigate).not.toHaveBeenCalled()

    await userEvent.keyboard('{Enter}')
    // 条件变化必须重置页码，否则会停在过滤后不存在的页码上
    expect(navigateSearchUpdater()({ page: 3, q: '' })).toMatchObject({
      page: 1,
      q: 'ops@bmc',
    })
  })

  it('翻页把页码写进 URL，并在末页禁用下一页', async () => {
    mocks.search = { page: 2, pageSize: 20 }
    mocks.useAuditLogsPageQuery.mockReturnValue({
      data: { data: ENTRIES, count: 40 },
      isPending: false,
      isError: false,
      isFetching: false,
      refetch: mocks.refetch,
    })

    const screen = await renderPage()

    // 20 条/页、共 40 条 → 第 2 页即末页
    expect(
      screen.getByRole('button', { name: '下一页' }).element()
    ).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: '上一页' }))
    expect(navigateSearchUpdater()({})).toMatchObject({ page: 1 })
  })

  it('无记录时给出空态文案', async () => {
    mocks.useAuditLogsPageQuery.mockReturnValue({
      data: { data: [], count: 0 },
      isPending: false,
      isError: false,
      isFetching: false,
      refetch: mocks.refetch,
    })

    const screen = await renderPage()
    await expect
      .element(screen.getByText('当前筛选条件下没有记录'))
      .toBeVisible()
  })

  it('加载失败时可重试', async () => {
    mocks.useAuditLogsPageQuery.mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      isFetching: false,
      refetch: mocks.refetch,
    })

    const screen = await renderPage()
    await expect.element(screen.getByText('审计日志加载失败')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(mocks.refetch).toHaveBeenCalled()
  })
})
