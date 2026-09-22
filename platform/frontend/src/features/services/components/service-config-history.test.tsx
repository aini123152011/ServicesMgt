import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type ServiceConfigVersion, type ServiceField } from '@/api/services'
import { ServiceConfigHistory } from './service-config-history'

const mocks = vi.hoisted(() => ({
  rollbackMutate: vi.fn(),
  permissions: { isAdmin: true, isOperator: true, roles: ['admin'] },
}))

const VERSIONS: ServiceConfigVersion[] = [
  {
    id: 'v3',
    version: 3,
    applied: true,
    rendered_digest: 'aaaaaaaaaaaaaaaa',
    user_email: 'alice@example.com',
    rolled_back_from: 1,
    created_at: '2026-09-21T03:00:00Z',
  },
  {
    id: 'v2',
    version: 2,
    applied: false,
    rendered_digest: 'bbbbbbbbbbbbbbbb',
    user_email: 'bob@example.com',
    rolled_back_from: null,
    created_at: '2026-09-21T02:00:00Z',
  },
]

vi.mock('../hooks/use-services', () => ({
  useConfigVersionsQuery: () => ({
    data: VERSIONS,
    isPending: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useConfigVersionDetailQuery: () => ({
    data: {
      version: 2,
      values: { port: 514, password: '********' },
      applied: false,
      rendered_digest: 'bbbbbbbbbbbbbbbb',
      user_email: 'op@example.com',
      rolled_back_from: null,
      created_at: '2026-09-21T02:00:00Z',
    },
    isPending: false,
    isError: false,
  }),
  useRollbackConfigMutation: () => ({
    mutate: mocks.rollbackMutate,
    isPending: false,
    variables: undefined,
  }),
}))

vi.mock('@/hooks/use-permissions', () => ({
  usePermissions: () => mocks.permissions,
}))

const FIELDS: ServiceField[] = [
  { name: 'port', label: '监听端口', type: 'integer' },
  { name: 'password', label: '共享密码', type: 'string', secret: true },
]

describe('ServiceConfigHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.permissions = { isAdmin: true, isOperator: true, roles: ['admin'] }
  })

  it('列出各版本的状态、操作者与回滚来源', async () => {
    const screen = await render(
      <ServiceConfigHistory name='samba' fields={FIELDS} />
    )

    await expect.element(screen.getByText('v3')).toBeInTheDocument()
    await expect.element(screen.getByText('v2')).toBeInTheDocument()
    // v3 是回滚产生的版本，要能一眼看出它不是手工下发
    await expect.element(screen.getByText('回滚自 v1')).toBeInTheDocument()
    await expect
      .element(screen.getByText('alice@example.com'))
      .toBeInTheDocument()
    await expect
      .element(screen.getByText('bob@example.com'))
      .toBeInTheDocument()
    await expect.element(screen.getByText('已生效')).toBeInTheDocument()
    await expect.element(screen.getByText('未生效')).toBeInTheDocument()
  })

  it('查看版本内容时按 schema 标签展示字段，secret 保持后端给的掩码', async () => {
    const screen = await render(
      <ServiceConfigHistory name='samba' fields={FIELDS} />
    )

    await userEvent.click(
      screen.getByRole('button', { name: '查看 v2 的配置' })
    )

    await expect.element(screen.getByText('监听端口')).toBeInTheDocument()
    await expect.element(screen.getByText('共享密码')).toBeInTheDocument()
    await expect.element(screen.getByText('514')).toBeInTheDocument()
    // 后端已脱敏，前端原样展示，绝不能出现明文
    await expect.element(screen.getByText('********')).toBeInTheDocument()
  })

  it('回滚需要二次确认，确认后带上版本号调用 mutation', async () => {
    const screen = await render(
      <ServiceConfigHistory name='samba' fields={FIELDS} />
    )

    await userEvent.click(screen.getByRole('button', { name: '回滚到 v3' }))
    // 未确认前不能动配置
    expect(mocks.rollbackMutate).not.toHaveBeenCalled()

    await expect
      .element(screen.getByText('回滚到版本 v3？'))
      .toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认回滚' }))

    expect(mocks.rollbackMutate).toHaveBeenCalledOnce()
    expect(mocks.rollbackMutate).toHaveBeenCalledWith(3)
  })

  it('只读角色不能回滚（按钮禁用且不发请求）', async () => {
    mocks.permissions = {
      isAdmin: false,
      isOperator: false,
      roles: ['readonly'],
    }
    const screen = await render(
      <ServiceConfigHistory name='samba' fields={FIELDS} />
    )

    await expect
      .element(screen.getByRole('button', { name: '回滚到 v3' }))
      .toBeDisabled()
    expect(mocks.rollbackMutate).not.toHaveBeenCalled()
  })
})
