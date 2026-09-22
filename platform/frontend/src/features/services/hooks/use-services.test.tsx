import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from 'vitest-browser-react'
import { type ServiceConfigVersion } from '@/api/services'
import {
  useConfigVersionsQuery,
  useRollbackConfigMutation,
} from './use-services'

const mocks = vi.hoisted(() => ({
  listVersions: vi.fn(),
  rollback: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
}))

// 只替换这两个接口，其余导出保持真实（use-services 还引用 getService 等）
vi.mock('@/api/services', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/services')>()
  return {
    ...actual,
    listServiceConfigVersions: mocks.listVersions,
    rollbackServiceConfigVersion: mocks.rollback,
  }
})

vi.mock('sonner', () => ({
  toast: { success: mocks.success, warning: mocks.warning },
}))

const VERSIONS: ServiceConfigVersion[] = [
  {
    id: 'v1',
    version: 1,
    applied: true,
    rendered_digest: 'aaaa',
    user_email: 'op@example.com',
    rolled_back_from: null,
    created_at: '2026-09-21T00:00:00Z',
  },
]

function wrapper(queryClient: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function newQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

describe('use-services 配置版本 hooks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listVersions.mockResolvedValue(VERSIONS)
    mocks.rollback.mockResolvedValue({ message: 'ok', applied: true })
  })

  it('版本列表用 services/<name>/configVersions 作为缓存键', async () => {
    const queryClient = newQueryClient()
    const { result } = await renderHook(() => useConfigVersionsQuery('samba'), {
      wrapper: wrapper(queryClient),
    })

    await expect.poll(() => result.current.data?.length).toBe(1)
    // 键必须与 useUpdateConfigMutation 失效的 ['services', name] 前缀对齐，否则保存后列表不刷新
    expect(
      queryClient.getQueryCache().find({
        queryKey: ['services', 'samba', 'configVersions'],
      })
    ).toBeTruthy()
    expect(mocks.listVersions).toHaveBeenCalledWith('samba')
  })

  it('回滚成功后失效服务详情与总览，并按 applied 选提示', async () => {
    const queryClient = newQueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = await renderHook(
      () => useRollbackConfigMutation('samba'),
      { wrapper: wrapper(queryClient) }
    )

    result.current.mutate(2)
    await expect.poll(() => mocks.rollback.mock.calls.length).toBe(1)

    expect(mocks.rollback).toHaveBeenCalledWith('samba', 2)
    await expect
      .poll(() =>
        invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))
      )
      .toContain(JSON.stringify(['services', 'samba']))
    await expect.poll(() => mocks.success.mock.calls.length).toBe(1)
    expect(mocks.warning).not.toHaveBeenCalled()
  })

  it('回滚后 applied=false（容器未运行）时给警示提示而不是成功提示', async () => {
    mocks.rollback.mockResolvedValue({ message: 'saved', applied: false })
    const { result } = await renderHook(
      () => useRollbackConfigMutation('samba'),
      { wrapper: wrapper(newQueryClient()) }
    )

    result.current.mutate(1)

    await expect.poll(() => mocks.warning.mock.calls.length).toBe(1)
    expect(mocks.success).not.toHaveBeenCalled()
  })
})
