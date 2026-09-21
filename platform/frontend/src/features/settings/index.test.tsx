import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SettingsLayout } from './index'

const mocks = vi.hoisted(() => ({
  usePermissions: vi.fn(),
}))

vi.mock('@/hooks/use-permissions', () => ({
  usePermissions: mocks.usePermissions,
}))

// 顶栏与子路由出口都属壳子，本次只验证子导航的渲染与角色显隐
vi.mock('@/components/layout/header', () => ({
  Header: ({ children }: { children?: React.ReactNode }) => (
    <header>{children}</header>
  ),
}))
vi.mock('@/components/search', () => ({ Search: () => null }))
vi.mock('@/components/language-switch', () => ({ LanguageSwitch: () => null }))
vi.mock('@/components/theme-switch', () => ({ ThemeSwitch: () => null }))
vi.mock('@/components/profile-dropdown', () => ({
  ProfileDropdown: () => null,
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    Outlet: () => null,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: '/settings/account' }),
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

describe('SettingsLayout 子导航', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('管理员看到四个分区', async () => {
    mocks.usePermissions.mockReturnValue({
      isAdmin: true,
      isOperator: true,
      roles: ['admin'],
    })

    const screen = await render(<SettingsLayout />)

    await expect
      .element(screen.getByRole('link', { name: /账号/ }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('link', { name: /外观/ }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('link', { name: /系统更新/ }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('link', { name: /关于/ }))
      .toBeInTheDocument()
  })

  it('非管理员不显示系统更新入口（后端接口仍强制 admin）', async () => {
    mocks.usePermissions.mockReturnValue({
      isAdmin: false,
      isOperator: false,
      roles: ['readonly'],
    })

    const screen = await render(<SettingsLayout />)

    await expect
      .element(screen.getByRole('link', { name: /账号/ }))
      .toBeInTheDocument()
    expect(screen.getByRole('link', { name: /系统更新/ }).query()).toBeNull()
  })
})
