import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { AccountPasswordForm } from './account-password-form'

const mocks = vi.hoisted(() => ({
  updateCurrentUserPassword: vi.fn(),
}))

// 与资料表单同样不 mock hook：断言落在最终接口调用与请求体字段名上
vi.mock('@/api/users', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/users')>()
  return {
    ...actual,
    updateCurrentUserPassword: mocks.updateCurrentUserPassword,
  }
})

function renderPasswordForm(): Promise<RenderResult> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AccountPasswordForm />
    </QueryClientProvider>
  )
}

async function fillPasswords(
  screen: RenderResult,
  values: { current: string; next: string; confirm: string }
) {
  // 「新密码」是「确认新密码」的子串，必须精确匹配才不会同时命中两个输入框
  await userEvent.fill(
    screen.getByLabelText('当前密码', { exact: true }),
    values.current
  )
  await userEvent.fill(
    screen.getByLabelText('新密码', { exact: true }),
    values.next
  )
  await userEvent.fill(
    screen.getByLabelText('确认新密码', { exact: true }),
    values.confirm
  )
}

describe('AccountPasswordForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.updateCurrentUserPassword.mockResolvedValue({
      message: 'Password updated successfully',
    })
  })

  it('提交时调用改密码接口，字段名与后端契约一致', async () => {
    const screen = await renderPasswordForm()

    await fillPasswords(screen, {
      current: 'old-password',
      next: 'new-password-1',
      confirm: 'new-password-1',
    })
    await userEvent.click(screen.getByRole('button', { name: /修改密码/ }))

    await vi.waitFor(() => {
      expect(mocks.updateCurrentUserPassword).toHaveBeenCalledWith({
        current_password: 'old-password',
        new_password: 'new-password-1',
      })
    })
  })

  it('两次新密码不一致时本地拦截，不发起请求', async () => {
    const screen = await renderPasswordForm()

    await fillPasswords(screen, {
      current: 'old-password',
      next: 'new-password-1',
      confirm: 'new-password-2',
    })
    await userEvent.click(screen.getByRole('button', { name: /修改密码/ }))

    await expect
      .element(screen.getByText('两次输入的新密码不一致'))
      .toBeInTheDocument()
    expect(mocks.updateCurrentUserPassword).not.toHaveBeenCalled()
  })

  it('新密码短于 8 位时本地拦截（与后端 min_length 对齐）', async () => {
    const screen = await renderPasswordForm()

    await fillPasswords(screen, {
      current: 'old-password',
      next: 'short',
      confirm: 'short',
    })
    await userEvent.click(screen.getByRole('button', { name: /修改密码/ }))

    await expect.element(screen.getByText('密码至少 8 位')).toBeInTheDocument()
    expect(mocks.updateCurrentUserPassword).not.toHaveBeenCalled()
  })
})
