import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type UserPublic } from '@/api/auth'
import { AccountProfileForm } from './account-profile-form'

const mocks = vi.hoisted(() => ({
  updateCurrentUser: vi.fn(),
  updateCurrentUserPassword: vi.fn(),
  setUser: vi.fn(),
}))

// 不 mock mutation hook：让「表单 → hook → api 封装」整条链路都真实执行，
// 断言落在最终的接口调用与请求体上，避免只验证了 hook 被调用
vi.mock('@/api/users', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/users')>()
  return {
    ...actual,
    updateCurrentUser: mocks.updateCurrentUser,
    updateCurrentUserPassword: mocks.updateCurrentUserPassword,
  }
})

const MOCK_USER: UserPublic = {
  id: 'user-1',
  email: 'alex@example.com',
  is_active: true,
  is_superuser: false,
  full_name: 'Alex Smith',
  roles: ['operator'],
  created_at: '2026-01-01T00:00:00Z',
}

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: (selector: (state: unknown) => unknown) =>
    selector({ auth: { user: MOCK_USER, setUser: mocks.setUser } }),
}))

function renderProfileForm(): Promise<RenderResult> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AccountProfileForm />
    </QueryClientProvider>
  )
}

describe('AccountProfileForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.updateCurrentUser.mockResolvedValue({
      ...MOCK_USER,
      full_name: 'Alexandra Smith',
      email: 'alexandra@example.com',
    })
  })

  it('用当前登录用户填充默认值', async () => {
    const screen = await renderProfileForm()

    await expect
      .element(screen.getByLabelText('姓名'))
      .toHaveValue('Alex Smith')
    await expect
      .element(screen.getByLabelText('邮箱'))
      .toHaveValue('alex@example.com')
  })

  it('提交时调用 PATCH /users/me 的封装并写入新值', async () => {
    const screen = await renderProfileForm()

    const nameInput = screen.getByLabelText('姓名')
    await userEvent.fill(nameInput, 'Alexandra Smith')
    await userEvent.fill(screen.getByLabelText('邮箱'), 'alexandra@example.com')
    await userEvent.click(screen.getByRole('button', { name: /保存资料/ }))

    await vi.waitFor(() => {
      expect(mocks.updateCurrentUser).toHaveBeenCalledWith({
        full_name: 'Alexandra Smith',
        email: 'alexandra@example.com',
      })
    })
    // 成功后要把返回的用户写回 auth store，否则页头姓名停在旧值
    await vi.waitFor(() => {
      expect(mocks.setUser).toHaveBeenCalledWith(
        expect.objectContaining({ full_name: 'Alexandra Smith' })
      )
    })
  })

  it('邮箱非法时本地拦截，不发起请求', async () => {
    const screen = await renderProfileForm()

    await userEvent.fill(screen.getByLabelText('邮箱'), 'not-an-email')
    await userEvent.click(screen.getByRole('button', { name: /保存资料/ }))

    await expect.element(screen.getByText('请输入有效邮箱')).toBeInTheDocument()
    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()
  })

  it('清空姓名提交 null，与后端可空语义一致', async () => {
    const screen = await renderProfileForm()

    await userEvent.fill(screen.getByLabelText('姓名'), '')
    await userEvent.click(screen.getByRole('button', { name: /保存资料/ }))

    await vi.waitFor(() => {
      expect(mocks.updateCurrentUser).toHaveBeenCalledWith({
        full_name: null,
        email: 'alex@example.com',
      })
    })
  })
})
