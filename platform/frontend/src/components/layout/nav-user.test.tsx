import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { NavUser } from './nav-user'

const mocks = vi.hoisted(() => ({
  isAdmin: true,
}))

vi.mock('@/hooks/use-permissions', () => ({
  usePermissions: () => ({
    isAdmin: mocks.isAdmin,
    isOperator: true,
    roles: [],
  }),
}))

// 当前登录用户：身份区要显示真实用户，而不是模板占位。
// 注意 useAuthStore 有两种调用方式——带选择器（nav-user）与不带（退出对话框），
// 替身两种都要支持，否则后者会拿到 undefined。
vi.mock('@/stores/auth-store', () => {
  const state = {
    auth: {
      user: { full_name: '张运维', email: 'ops@bmcplatform.cn' },
      accessToken: 'test-token',
      setUser: () => {},
      setAccessToken: () => {},
      reset: () => {},
    },
  }
  return {
    useAuthStore: (selector?: (current: typeof state) => unknown) =>
      selector ? selector(state) : state,
  }
})

// 菜单项是 router Link，退出对话框还用了 useNavigate/useLocation；
// 本测试只关心菜单契约，这几个都换成替身，免得为它拉起 RouterProvider
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: '/', href: '/' }),
    Link: ({
      to,
      children,
      ...rest
    }: {
      to: string
      children?: React.ReactNode
    } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
      <a href={to} {...rest}>
        {children}
      </a>
    ),
  }
})

async function openMenu() {
  const screen = await render(
    <SidebarProvider>
      <NavUser />
    </SidebarProvider>
  )
  await userEvent.click(screen.getByRole('button', { name: /张运维/ }))
  await expect.element(screen.getByRole('menu')).toBeInTheDocument()
  return screen
}

/** 菜单里的可点项（文本 → 链接） */
function menuEntries(screen: Awaited<ReturnType<typeof openMenu>>) {
  const menu = screen.getByRole('menu').element()
  return [...menu.querySelectorAll('a')].map((a) => ({
    text: a.textContent?.trim(),
    href: a.getAttribute('href'),
  }))
}

describe('NavUser', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.isAdmin = true
  })

  it('管理员菜单含四个设置入口与退出登录，链接指向设置子页', async () => {
    const screen = await openMenu()

    expect(menuEntries(screen)).toEqual([
      { text: '账号', href: '/settings/account' },
      { text: '外观', href: '/settings/appearance' },
      { text: '系统更新', href: '/settings/updates' },
      { text: '关于', href: '/settings/about' },
    ])
    expect(
      screen.getByRole('menuitem', { name: /退出登录/ }).element()
    ).toBeInTheDocument()
  })

  it('非管理员看不到「系统更新」（后端接口强制 admin）', async () => {
    mocks.isAdmin = false

    const screen = await openMenu()

    expect(menuEntries(screen).map((entry) => entry.text)).toEqual([
      '账号',
      '外观',
      '关于',
    ])
  })

  it('身份区显示当前登录用户而不是模板占位', async () => {
    const screen = await openMenu()

    // 触发按钮与菜单标签都显示同一身份，故按菜单整体断言，避免定位到两个同名节点
    const menuText = screen.getByRole('menu').element().textContent ?? ''
    expect(menuText).toContain('张运维')
    expect(menuText).toContain('ops@bmcplatform.cn')
  })
})
