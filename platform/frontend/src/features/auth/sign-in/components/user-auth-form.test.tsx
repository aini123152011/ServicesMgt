import { AxiosError, type AxiosResponse } from 'axios'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { type Locator, userEvent } from 'vitest/browser'
import {
  fetchCaptcha,
  getCurrentUser,
  getRegistrationAvailability,
  loginAccessToken,
} from '@/api/auth'
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
  email_verified_at: '2026-09-20T00:00:00Z',
}

const navigate = vi.fn()
const setUserMock = vi.fn()
const setAccessTokenMock = vi.fn()
const mockedLogin = vi.mocked(loginAccessToken)
const mockedMe = vi.mocked(getCurrentUser)
const mockedCaptcha = vi.mocked(fetchCaptcha)
const mockedAvailability = vi.mocked(getRegistrationAvailability)

vi.mock('@/api/auth', () => ({
  loginAccessToken: vi.fn(),
  getCurrentUser: vi.fn(),
  fetchCaptcha: vi.fn(),
  getRegistrationAvailability: vi.fn(),
}))

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({
    auth: {
      setUser: setUserMock,
      setAccessToken: setAccessTokenMock,
      // 登录失败路径会调用它清掉半套凭证；mock 里缺了会抛 TypeError，navigate 就永远执行不到
      reset: vi.fn(),
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

/** 验证码字段走 TanStack Query，渲染时必须给 Provider */
function renderForm(props: { redirectTo?: string } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <UserAuthForm {...props} />
    </QueryClientProvider>
  )
}

/** 构造「邮箱未验证」的后端错误（登录流程据此跳验证码页） */
function unverifiedError(): AxiosError {
  return new AxiosError(
    'Request failed',
    'ERR_BAD_REQUEST',
    undefined,
    undefined,
    {
      data: { detail: 'Email is not verified' },
      status: 400,
      statusText: 'Bad Request',
      headers: {},
      config: {} as never,
    } as AxiosResponse
  )
}

describe('UserAuthForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedLogin.mockResolvedValue({ ...mockToken })
    mockedMe.mockResolvedValue({ ...mockUser })
    mockedCaptcha.mockResolvedValue({
      captcha_id: 'captcha-1',
      image: 'data:image/png;base64,AAAA',
    })
    mockedAvailability.mockResolvedValue({
      enabled: true,
      email_configured: true,
    })
  })

  describe('Rendering without redirectTo', () => {
    let screen: RenderResult
    let emailInput: Locator
    let passwordInput: Locator
    let captchaInput: Locator
    let signInButton: Locator
    let forgotPasswordLink: Locator

    beforeEach(async () => {
      screen = await renderForm()
      emailInput = screen.getByRole('textbox', { name: /^邮箱$/ })
      passwordInput = screen.getByLabelText(/^密码$/i)
      captchaInput = screen.getByLabelText(/^图片验证码$/)
      signInButton = screen.getByRole('button', { name: /^登录$/ })
      forgotPasswordLink = screen.getByText(/^忘记密码？$/)
    })

    it('renders fields, captcha, submit button, and forgot password link', async () => {
      await expect.element(emailInput).toBeInTheDocument()
      await expect.element(passwordInput).toBeInTheDocument()
      await expect.element(captchaInput).toBeInTheDocument()
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
      await userEvent.fill(captchaInput, 'AB12')

      await userEvent.click(signInButton)

      // 验证码是必填项：登录请求必须把票据 id 与用户填的字符一起带上
      await vi.waitFor(() =>
        expect(mockedLogin).toHaveBeenCalledWith('a@b.com', '1234567', {
          captchaId: 'captcha-1',
          captchaAnswer: 'AB12',
        })
      )
      await vi.waitFor(() =>
        expect(setAccessTokenMock).toHaveBeenCalledWith('jwt-access-token')
      )
      expect(setUserMock).toHaveBeenCalledWith(mockUser)

      await vi.waitFor(() =>
        expect(navigate).toHaveBeenCalledWith({ to: '/', replace: true })
      )
    })

    it('jumps to the verification page when the email is not verified', async () => {
      mockedLogin.mockRejectedValue(unverifiedError())

      await userEvent.fill(emailInput, 'a@b.com')
      await userEvent.fill(passwordInput, '1234567')
      await userEvent.fill(captchaInput, 'AB12')
      await userEvent.click(signInButton)

      await vi.waitFor(() =>
        expect(navigate).toHaveBeenCalledWith({
          to: '/otp',
          search: { email: 'a@b.com' },
          replace: true,
        })
      )
      // 未验证时不该留下半套凭证
      expect(setAccessTokenMock).not.toHaveBeenCalled()
    })
  })

  it('navigates to redirectTo when provided', async () => {
    const { getByRole, getByLabelText } = await renderForm({
      redirectTo: '/services',
    })

    await userEvent.fill(getByRole('textbox', { name: /邮箱/ }), 'a@b.com')
    await userEvent.fill(getByLabelText('密码'), '1234567')
    await userEvent.fill(getByLabelText(/^图片验证码$/), 'AB12')

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

describe('UserAuthForm registration entry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedCaptcha.mockResolvedValue({
      captcha_id: 'captcha-1',
      image: 'data:image/png;base64,AAAA',
    })
  })

  it('hides the sign-up entry when registration is disabled', async () => {
    mockedAvailability.mockResolvedValue({
      enabled: false,
      email_configured: true,
    })
    const { getByText, getByRole } = await renderForm()

    await vi.waitFor(() => expect(mockedAvailability).toHaveBeenCalled())
    // 文案在 <p> 里与链接连排，锚定正则匹配不到，用包含匹配 + locator.query()
    expect(getByText(/还没有账号？/).query()).toBeNull()
    await expect
      .element(getByRole('button', { name: /^登录$/ }))
      .toBeInTheDocument()
  })

  it('shows the sign-up entry when registration is enabled', async () => {
    mockedAvailability.mockResolvedValue({
      enabled: true,
      email_configured: true,
    })
    const { getByText } = await renderForm()

    await expect.element(getByText(/还没有账号？/)).toBeInTheDocument()
  })
})
