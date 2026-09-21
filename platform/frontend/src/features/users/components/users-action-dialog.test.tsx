import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, type RenderResult } from 'vitest-browser-react'
import { type UserEvent, userEvent } from 'vitest/browser'
import { type UserPublic } from '@/api/auth'
import { UsersActionDialog } from './users-action-dialog'

const mutationMocks = vi.hoisted(() => ({
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
}))

vi.mock('../hooks/use-users', () => ({
  useCreateUserMutation: () => ({
    mutate: mutationMocks.createMutate,
    isPending: false,
  }),
  useUpdateUserMutation: () => ({
    mutate: mutationMocks.updateMutate,
    isPending: false,
  }),
  useRolesQuery: () => ({
    data: ['admin', 'operator', 'readonly'],
    isPending: false,
  }),
}))

const MOCK_USER: UserPublic = {
  id: 'alex_uuid',
  email: 'alex@smith.com',
  is_active: true,
  is_superuser: false,
  full_name: 'Alex Smith',
  roles: ['operator'],
  created_at: '2026-01-01T00:00:00Z',
}

describe('UsersActionDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 组件在 mutate 的 onSuccess 里重置表单并关闭对话框，
    // mock 不回调的话关闭链路永远不触发（测试会断言 onOpenChange(false)）
    mutationMocks.createMutate.mockImplementation(
      (_payload: unknown, opts?: { onSuccess?: () => void }) =>
        opts?.onSuccess?.()
    )
    mutationMocks.updateMutate.mockImplementation(
      (_args: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.()
    )
  })

  describe('create user', () => {
    it('renders title and description', async () => {
      const { getByRole, getByText } = await render(
        <UsersActionDialog open onOpenChange={vi.fn()} />
      )

      const title = getByRole('heading', { level: 2, name: /新建用户/i })
      const description = getByText(/创建平台用户并分配角色/)

      await expect.element(title).toBeInTheDocument()
      await expect.element(description).toBeInTheDocument()
    })

    it('shows validation messages when the form is submitted with empty fields', async () => {
      const { getByRole, getByText } = await render(
        <UsersActionDialog open onOpenChange={vi.fn()} />
      )

      const submitButton = getByRole('button', { name: /保存/i })
      await userEvent.click(submitButton)

      await expect.element(getByText('请输入邮箱。')).toBeInTheDocument()
      await expect.element(getByText('请输入密码。')).toBeInTheDocument()
    })

    it('shows password validation messages when password is invalid', async () => {
      const { getByRole, getByText, getByLabelText } = await render(
        <UsersActionDialog open onOpenChange={vi.fn()} />
      )

      const password = getByLabelText(/^密码$/)
      const confirmPassword = getByLabelText(/^确认密码$/)
      const submitButton = getByRole('button', { name: /保存/i })

      await userEvent.fill(password, 'a')
      await userEvent.fill(confirmPassword, 'b')
      await userEvent.click(submitButton)
      await expect
        .element(getByText('两次输入的密码不一致。'))
        .toBeInTheDocument()

      await userEvent.fill(password, 'short1a')
      await expect
        .element(getByText('密码长度至少 8 个字符。'))
        .toBeInTheDocument()

      await userEvent.fill(password, '12345678')
      await expect
        .element(getByText('密码需至少包含一个小写字母。'))
        .toBeInTheDocument()

      await userEvent.fill(password, 'onlylowercase')
      await expect
        .element(getByText('密码需至少包含一个数字。'))
        .toBeInTheDocument()
    })

    it('calls the create mutation with form values and closes the dialog', async () => {
      const onOpenChange = vi.fn()
      const screen = await render(
        <UsersActionDialog open onOpenChange={onOpenChange} />
      )

      await fillCommonFields(userEvent, screen, {
        email: 'a@b.co',
        fullName: '张三',
        password: 'S3cur3P@ssw0rd',
      })
      // 默认已勾选只读，再勾选管理员
      await userEvent.click(screen.getByRole('checkbox', { name: '管理员' }))

      await userEvent.click(screen.getByRole('button', { name: /保存/i }))

      expect(mutationMocks.createMutate).toHaveBeenCalledOnce()
      expect(mutationMocks.createMutate).toHaveBeenCalledWith(
        {
          email: 'a@b.co',
          password: 'S3cur3P@ssw0rd',
          full_name: '张三',
          roles: ['readonly', 'admin'],
        },
        expect.any(Object)
      )
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })

  describe('edit user', () => {
    it('renders title and prefilled values', async () => {
      const { getByRole, getByLabelText } = await render(
        <UsersActionDialog open onOpenChange={vi.fn()} currentRow={MOCK_USER} />
      )

      const title = getByRole('heading', { level: 2, name: /编辑用户/i })
      await expect.element(title).toBeInTheDocument()
      await expect
        .element(getByLabelText(/^邮箱$/))
        .toHaveValue(MOCK_USER.email)
    })

    it('submits without password changes', async () => {
      const onOpenChange = vi.fn()
      const screen = await render(
        <UsersActionDialog
          open
          onOpenChange={onOpenChange}
          currentRow={MOCK_USER}
        />
      )

      await userEvent.click(screen.getByRole('button', { name: /保存/i }))

      expect(mutationMocks.updateMutate).toHaveBeenCalledOnce()
      expect(mutationMocks.updateMutate).toHaveBeenCalledWith(
        {
          id: MOCK_USER.id,
          payload: {
            email: MOCK_USER.email,
            full_name: MOCK_USER.full_name,
            is_active: true,
            roles: MOCK_USER.roles,
          },
        },
        expect.any(Object)
      )
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })

    it('sends password only when it is changed', async () => {
      const screen = await render(
        <UsersActionDialog open onOpenChange={vi.fn()} currentRow={MOCK_USER} />
      )

      await fillCommonFields(userEvent, screen, {
        password: 'N3wPassw0rd',
      })

      await userEvent.click(screen.getByRole('button', { name: /保存/i }))

      expect(mutationMocks.updateMutate).toHaveBeenCalledOnce()
      const [call] = mutationMocks.updateMutate.mock.calls
      expect(call?.[1]?.onSuccess).toBeTypeOf('function')
      expect(call?.[0]?.payload.password).toBe('N3wPassw0rd')
    })

    it('requires at least one role', async () => {
      const { getByRole, getByText } = await render(
        <UsersActionDialog open onOpenChange={vi.fn()} currentRow={MOCK_USER} />
      )

      // 取消全部已选角色后提交
      await userEvent.click(getByRole('checkbox', { name: '操作员' }))
      await userEvent.click(getByRole('button', { name: /保存/i }))

      await expect.element(getByText('至少选择一个角色。')).toBeInTheDocument()
      expect(mutationMocks.updateMutate).not.toHaveBeenCalled()
    })
  })
})

async function fillCommonFields(
  user: UserEvent,
  screen: RenderResult,
  overrides?: {
    email?: string
    fullName?: string
    password?: string
  }
) {
  if (overrides?.email !== undefined) {
    await user.fill(screen.getByLabelText(/^邮箱$/), overrides.email)
  }
  if (overrides?.fullName !== undefined) {
    await user.fill(screen.getByLabelText(/^姓名$/), overrides.fullName)
  }
  if (overrides?.password !== undefined) {
    const password = screen.getByLabelText(/^密码$/)
    const confirmPassword = screen.getByLabelText(/^确认密码$/)
    await user.fill(password, overrides.password)
    await user.fill(confirmPassword, overrides.password)
  }
}
