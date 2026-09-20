import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type UserPublic } from '@/api/auth'
import { UsersDeleteDialog } from './users-delete-dialog'

const mutationMocks = vi.hoisted(() => ({
  deleteMutate: vi.fn(),
}))

vi.mock('../hooks/use-users', () => ({
  useDeleteUserMutation: () => ({
    mutate: mutationMocks.deleteMutate,
    isPending: false,
  }),
}))

const MOCK_USER: UserPublic = {
  id: 'user-delete-test',
  email: 'jane@example.com',
  is_active: true,
  is_superuser: false,
  full_name: 'Jane Doe',
  roles: ['operator'],
  created_at: '2026-01-01T00:00:00Z',
}

describe('UsersDeleteDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 模拟 mutation 成功回调，验证对话框关闭链路
    mutationMocks.deleteMutate.mockImplementation(
      (_id: string, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.()
    )
  })

  it('renders title, description, input and buttons', async () => {
    const { getByText, getByRole } = await render(
      <UsersDeleteDialog open onOpenChange={vi.fn()} currentRow={MOCK_USER} />
    )

    const title = getByRole('heading', { level: 2, name: /删除用户/i })
    const desc = getByText(new RegExp(`确定要删除 ${MOCK_USER.email}`, 'i'))
    const emailInput = getByRole('textbox', { name: /邮箱/i })
    const cancelButton = getByRole('button', { name: /取消/i })
    const deleteButton = getByRole('button', { name: /删除/i })

    await expect.element(title).toBeInTheDocument()
    await expect.element(desc).toBeInTheDocument()
    await expect.element(emailInput).toBeInTheDocument()
    await expect.element(cancelButton).toBeInTheDocument()
    await expect.element(deleteButton).toBeInTheDocument()
    await expect.element(deleteButton).toBeDisabled()
  })

  it('keeps the delete button disabled until the email input matches', async () => {
    const { getByRole } = await render(
      <UsersDeleteDialog open onOpenChange={vi.fn()} currentRow={MOCK_USER} />
    )

    const emailInput = getByRole('textbox', { name: /邮箱/i })
    const deleteButton = getByRole('button', { name: /删除/i })

    await expect.element(deleteButton).toBeDisabled()

    await userEvent.fill(emailInput, 'wrong@example.com')
    await expect.element(deleteButton).toBeDisabled()

    await userEvent.fill(emailInput, MOCK_USER.email)
    await expect.element(deleteButton).toBeEnabled()
  })

  it('calls the delete mutation with the user id and closes the dialog', async () => {
    const onOpenChange = vi.fn()
    const { getByRole } = await render(
      <UsersDeleteDialog
        open
        onOpenChange={onOpenChange}
        currentRow={MOCK_USER}
      />
    )

    const emailInput = getByRole('textbox', { name: /邮箱/i })
    await userEvent.fill(emailInput, MOCK_USER.email)
    await userEvent.click(getByRole('button', { name: /删除/i }))

    expect(mutationMocks.deleteMutate).toHaveBeenCalledOnce()
    expect(mutationMocks.deleteMutate).toHaveBeenCalledWith(
      MOCK_USER.id,
      expect.any(Object)
    )
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('does not delete when the confirm email does not match', async () => {
    const onOpenChange = vi.fn()
    const { getByRole } = await render(
      <UsersDeleteDialog
        open
        onOpenChange={onOpenChange}
        currentRow={MOCK_USER}
      />
    )

    const emailInput = getByRole('textbox', { name: /邮箱/i })
    await userEvent.fill(emailInput, 'wrong@example.com')
    await userEvent.click(getByRole('button', { name: /删除/i }))

    expect(mutationMocks.deleteMutate).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('closes the dialog when the cancel button is clicked', async () => {
    const onOpenChange = vi.fn()
    const { getByRole } = await render(
      <UsersDeleteDialog
        open
        onOpenChange={onOpenChange}
        currentRow={MOCK_USER}
      />
    )

    await userEvent.click(getByRole('button', { name: /取消/i }))

    expect(onOpenChange).toHaveBeenCalledOnce()
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(mutationMocks.deleteMutate).not.toHaveBeenCalled()
  })
})
