import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { type AuditLogEntry } from '@/api/audit'
import { type ServiceStatusResponse, type ServiceSummary } from '@/api/services'
import { Home } from './index'

const mocks = vi.hoisted(() => ({
  useServicesQuery: vi.fn(),
  useServicesStatusQuery: vi.fn(),
  useAuditLogsQuery: vi.fn(),
  usePermissions: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('@/features/services/hooks/use-services', () => ({
  useServicesQuery: mocks.useServicesQuery,
  useServicesStatusQuery: mocks.useServicesStatusQuery,
}))

vi.mock('./hooks/use-home', () => ({
  useAuditLogsQuery: mocks.useAuditLogsQuery,
  useResetFaultModeMutation: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@/hooks/use-permissions', () => ({
  usePermissions: mocks.usePermissions,
}))

// 顶栏是公共壳子（搜索/主题/身份），与本页的数据断言无关：整块替换掉，
// 免得为它拉起 SidebarProvider、SearchProvider 与命令面板
vi.mock('@/components/layout/header', () => ({
  Header: ({ children }: { children?: React.ReactNode }) => (
    <header>{children}</header>
  ),
}))
vi.mock('@/components/layout/header-actions', () => ({
  HeaderActions: () => null,
}))
vi.mock('@/components/language-switch', () => ({ LanguageSwitch: () => null }))
vi.mock('@/components/theme-switch', () => ({ ThemeSwitch: () => null }))
vi.mock('@/components/profile-dropdown', () => ({
  ProfileDropdown: () => null,
}))

// 首页只用到 Link 与 useNavigate，无需真起一个 router（路由与鉴权与本次断言无关）
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    Link: ({
      to,
      params,
      children,
      ...rest
    }: {
      to: string
      params?: Record<string, string>
      children?: React.ReactNode
    } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
      <a href={to} {...rest}>
        {children}
      </a>
    ),
  }
})

const SERVICES: ServiceSummary[] = [
  {
    name: 'nginx',
    display_name: '文件共享服务',
    category: 'file-share',
    description: null,
    container_name: 'bmc-nginx',
    ports: [{ port: 8080, protocol: 'tcp', description: null }],
    reload_mode: 'hot',
  },
  {
    name: 'rsyslog',
    display_name: '日志接收服务',
    category: 'log-monitor',
    description: null,
    container_name: 'bmc-rsyslog',
    ports: [{ port: 514, protocol: 'udp', description: null }],
    reload_mode: 'restart',
  },
  {
    name: 'chrony',
    display_name: '时间同步服务',
    category: 'time',
    description: null,
    container_name: 'bmc-chrony',
    ports: [{ port: 123, protocol: 'udp', description: null }],
    reload_mode: 'restart',
  },
]

const STATUSES: Record<string, ServiceStatusResponse> = {
  nginx: { name: 'nginx', running: true, health: 'healthy', status: 'running' },
  rsyslog: {
    name: 'rsyslog',
    running: true,
    health: 'unhealthy',
    status: 'running',
  },
  chrony: { name: 'chrony', running: false, health: null, status: 'exited' },
}

const AUDIT_ENTRIES: AuditLogEntry[] = [
  {
    id: 'log-1',
    user_email: 'admin@example.com',
    action: 'service.restart',
    service_name: 'nginx',
    detail: 'container=bmc-nginx',
    created_at: '2026-09-21T08:00:00Z',
  },
]

/**
 * 统计卡的值：按卡片描述文本定位到卡片，取卡片内除标签外的文本。
 * 用 container 查询而不是 getByText：标签文本（如「运行中」）在健康表徽章里也会出现。
 */
function statValue(screen: RenderResult, label: string): string {
  const cards = screen.container.querySelectorAll('[data-slot="card"]')
  const card = Array.from(cards).find(
    (element) =>
      element.querySelector('[data-slot="card-description"]')?.textContent ===
      label
  )
  return card?.textContent?.replace(label, '') ?? ''
}

/** 卡片标题是否渲染（区分标题与正文里恰好包含同一词的情况） */
function hasCardTitle(screen: RenderResult, title: string): boolean {
  return Array.from(
    screen.container.querySelectorAll('[data-slot="card-title"]')
  ).some((element) => element.textContent === title)
}

async function renderHome() {
  return await render(<Home />)
}

describe('Home', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useServicesQuery.mockReturnValue({
      data: SERVICES,
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    })
    mocks.useServicesStatusQuery.mockReturnValue({
      data: STATUSES,
      isPending: false,
    })
    mocks.useAuditLogsQuery.mockReturnValue({
      data: AUDIT_ENTRIES,
      isPending: false,
    })
    mocks.usePermissions.mockReturnValue({
      isAdmin: true,
      isOperator: true,
      roles: ['admin'],
    })
  })

  it('统计卡数字来自服务列表与各服务状态', async () => {
    const screen = await renderHome()

    // 总数来自服务列表，其余三项按状态汇总（2 运行 / 1 健康异常 / 1 未运行）
    await expect.element(screen.getByText('服务总数')).toBeInTheDocument()
    expect(statValue(screen, '服务总数')).toBe('3')
    expect(statValue(screen, '运行中')).toBe('2')
    expect(statValue(screen, '健康异常')).toBe('1')
    expect(statValue(screen, '未运行')).toBe('1')
  })

  it('健康表按状态渲染徽章，点击行进入服务详情', async () => {
    const screen = await renderHome()

    await expect.element(screen.getByText('日志接收服务')).toBeInTheDocument()
    await expect.element(screen.getByText('无健康检查')).toBeInTheDocument()

    const nginxRow = screen.getByText('文件共享服务').element().closest('tr')
    expect(nginxRow?.textContent).toContain('健康')

    nginxRow?.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/services/$serviceName',
      params: { serviceName: 'nginx' },
    })
  })

  it('状态未就绪时统计卡显示骨架而不是 0', async () => {
    mocks.useServicesStatusQuery.mockReturnValue({
      data: undefined,
      isPending: true,
    })

    const screen = await renderHome()

    // 状态还没回来时不能把「未知」当成 0 显示
    expect(statValue(screen, '运行中')).toBe('')
    expect(statValue(screen, '健康异常')).toBe('')
  })

  it('没有纳管服务时统计卡显示 0 而不是一直转圈', async () => {
    mocks.useServicesQuery.mockReturnValue({
      data: [],
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    })
    // 列表为空时状态查询被禁用，isPending 会一直是 true
    mocks.useServicesStatusQuery.mockReturnValue({
      data: undefined,
      isPending: true,
    })

    const screen = await renderHome()

    expect(statValue(screen, '服务总数')).toBe('0')
    expect(statValue(screen, '运行中')).toBe('0')
    expect(statValue(screen, '未运行')).toBe('0')
  })

  it('管理员渲染最近变更并请求审计', async () => {
    const screen = await renderHome()

    await expect
      .element(screen.getByText('service.restart'))
      .toBeInTheDocument()
    expect(hasCardTitle(screen, '最近变更')).toBe(true)
    expect(mocks.useAuditLogsQuery).toHaveBeenCalledWith(true)
  })

  it('非管理员不请求审计，也不渲染最近变更卡', async () => {
    mocks.usePermissions.mockReturnValue({
      isAdmin: false,
      isOperator: false,
      roles: ['readonly'],
    })

    const screen = await renderHome()

    await expect.element(screen.getByText('快捷入口')).toBeInTheDocument()
    expect(mocks.useAuditLogsQuery).toHaveBeenCalledWith(false)
    // 既不请求也不留空卡片：只读用户看不到审计入口
    expect(hasCardTitle(screen, '最近变更')).toBe(false)
    expect(screen.getByText('service.restart').query()).toBeNull()
  })

  it('快捷入口的日志浏览指向日志监控类服务', async () => {
    const screen = await renderHome()

    const logLink = screen.getByRole('link', { name: /日志浏览/ })
    await expect.element(logLink).toBeInTheDocument()
    expect(logLink.element().getAttribute('href')).toBe(
      '/services/$serviceName'
    )
  })
})

describe('故障注入面板', () => {
  it('全部服务为 none 时提示「全部正常」', async () => {
    mocks.useServicesQuery.mockReturnValue({
      data: [{ ...SERVICES[0], fault_mode: 'none' }],
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    })
    mocks.useServicesStatusQuery.mockReturnValue({
      data: STATUSES,
      isPending: false,
    })
    const screen = await render(<Home />)

    await expect
      .element(screen.getByText(/全部服务均处于正常模式/))
      .toBeInTheDocument()
  })

  it('处于非 none 模式时列出服务、模式与复位按钮', async () => {
    mocks.useServicesQuery.mockReturnValue({
      data: [{ ...SERVICES[0], fault_mode: 'stratum_16' }],
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    })
    mocks.useServicesStatusQuery.mockReturnValue({
      data: STATUSES,
      isPending: false,
    })
    const screen = await render(<Home />)

    await expect.element(screen.getByText('stratum_16')).toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: /复位为正常/ }))
      .toBeInTheDocument()
  })
})
