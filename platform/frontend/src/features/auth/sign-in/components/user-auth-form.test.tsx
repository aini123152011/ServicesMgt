import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { type Locator, userEvent } from 'vitest/browser'
import { getCurrentUser, loginAccessToken } from '@/api/auth'
import { UserAuthForm } from './user-auth-form'

// 默认语言是中文（见 src/lib/i18n.ts），组件测试断言中文文案
const FORM_MESSAGES = {
  emailEmpty: '请输入邮箱。',
  passwordEmpty: '请输入密码。',
  passwordShort: '密码长度至少 7 个字符。',
} as const

const mockToken = { access_token: 'jwt-access-token', token_type: 'bearer' }
const mockUser = {
  id: '3fa85f64-5717-4562-b3fc-2c963f66afa6',
  email: 'a@b.com',
  is_active: true,
  is_superuser: false,
  full_name: 'Test User',
  roles: ['operator'],
  created_at: '2026-09-20T00:00:00Z',
}

const navigate = vi.fn()
const setUserMock = vi.fn()
const setAccessTokenMock = vi.fn()
const mockedLogin = vi.mocked(loginAccessToken)
const mockedMe = vi.mocked(getCurrentUser)

vi.mock('@/api/auth', () => ({
  loginAccessToken: vi.fn(),
  getCurrentUser: vi.fn(),
}))

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({
    auth: {
      setUser: setUserMock,
      setAccessToken: setAccessTokenMock,
    },
  }),
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigate,
    Link: ({
      children,
      to,
      className,
      ...rest
    }: {
      children?: React.ReactNode
      to: string
      className?: string
    }) => (
      <a href={to} className={className} {...rest}>
        {children}
      </a>
    ),
  }
})

describe('UserAuthForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedLogin.mockResolvedValue({ ...mockToken })
    mockedMe.mockResolvedValue({ ...mockUser })
  })

  describe('Rendering without redirectTo', () => {
    let screen: RenderResult
    let emailInput: Locator
    let passwordInput: Locator
    let signInButton: Locator
    let forgotPasswordLink: Locator

    beforeEach(async () => {
      screen = await render(<UserAuthForm />)
      emailInput = screen.getByRole('textbox', { name: /^邮箱$/ })
      passwordInput = screen.getByLabelText(/^密码$/i)
      signInButton = screen.getByRole('button', { name: /^登录$/ })
      forgotPasswordLink = screen.getByText(/^忘记密码？$/)
    })

    it('renders fields, submit button, and forgot password link', async () => {
      await expect.element(emailInput).toBeInTheDocument()
      await expect.element(passwordInput).toBeInTheDocument()
      await expect.element(signInButton).toBeInTheDocument()
      await expect.element(forgotPasswordLink).toBeInTheDocument()
    })

    it('shows validation messages when submitting empty form', async () => {
      await userEvent.click(signInButton)

      await expect
        .element(screen.getByText(FORM_MESSAGES.emailEmpty))
        .toBeInTheDocument()
      await expect
        .element(screen.getByText(FORM_MESSAGES.passwordEmpty))
        .toBeInTheDocument()
    })

    it('authenticates and navigates to default route on success', async () => {
      await userEvent.fill(emailInput, 'a@b.com')
      await userEvent.fill(passwordInput, '1234567')

      await userEvent.click(signInButton)

      await vi.waitFor(() =>
        expect(mockedLogin).toHaveBeenCalledWith('a@b.com', '1234567')
      )
      await vi.waitFor(() =>
        expect(setAccessTokenMock).toHaveBeenCalledWith('jwt-access-token')
      )
      expect(setUserMock).toHaveBeenCalledWith(mockUser)

      await vi.waitFor(() =>
        expect(navigate).toHaveBeenCalledWith({ to: '/', replace: true })
      )
    })
  })

  it('navigates to redirectTo when provided', async () => {
    const { getByRole, getByLabelText } = await render(
      <UserAuthForm redirectTo='/services' />
    )

    await userEvent.fill(getByRole('textbox', { name: /邮箱/ }), 'a@b.com')
    await userEvent.fill(getByLabelText('密码'), '1234567')

    await userEvent.click(getByRole('button', { name: /登录/ }))

    await vi.waitFor(() => expect(setUserMock).toHaveBeenCalledOnce())
    expect(setAccessTokenMock).toHaveBeenCalledWith('jwt-access-token')

    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/services',
        replace: true,
      })
    )
  })
})
