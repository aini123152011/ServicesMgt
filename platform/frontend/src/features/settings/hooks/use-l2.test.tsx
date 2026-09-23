import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from 'vitest-browser-react'
import { type L2ApplyResult, type L2State } from '@/api/l2'
import {
  useL2ApplyMutation,
  useL2DisableMutation,
  useL2StatusQuery,
} from './use-l2'

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  apply: vi.fn(),
  disable: vi.fn(),
}))

// 只替换这三个请求函数，其余导出保持真实
vi.mock('@/api/l2', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/l2')>()
  return {
    ...actual,
    getL2Status: mocks.status,
    applyL2Config: mocks.apply,
    disableL2: mocks.disable,
  }
})

const STATE: L2State = {
  enabled: true,
  env_path: '/host-deploy/.env',
  env_available: true,
  env_error: '',
  parent_iface: 'enp125s0f1',
  l2_subnet: '192.168.90.0/24',
  l2_gateway: '192.168.90.1',
  l2_subnet_v6: 'fd00:90::/64',
  l2_gateway_v6: 'fd00:90::1',
  l2_services: ['dhcp'],
  duplicate_keys: [],
  network: 'servicesmgt_dhcp-l2-net',
  network_exists: true,
  network_parent: 'enp125s0f1',
  attached: true,
  address: '192.168.90.2',
  address_v6: 'fd00:90::2',
  drift: [],
  checks: [{ level: 'ok', code: 'l2_ok', service: null, message: 'ok' }],
  candidates: [],
  nmcli_commands: [],
  default_iface: 'enp125s0f0',
}

const APPLIED: L2ApplyResult = {
  applied: true,
  rolled_back: false,
  steps: ['已改写 .env'],
  service_config: null,
  message: '二层夹具已启用。',
}

function wrapper(queryClient: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useL2StatusQuery', () => {
  it('用 l2/status 作为 queryKey 并返回状态', async () => {
    mocks.status.mockResolvedValue(STATE)
    const client = newClient()

    const { result } = await renderHook(() => useL2StatusQuery(0), {
      wrapper: wrapper(client),
    })

    await expect
      .poll(() => result.current.data?.parent_iface)
      .toBe('enp125s0f1')
    expect(client.getQueryData(['l2', 'status'])).toEqual(STATE)
  })

  it('请求失败时把错误暴露给调用方（由全局拦截处理提示）', async () => {
    mocks.status.mockRejectedValue(new Error('boom'))
    const client = newClient()

    const { result } = await renderHook(() => useL2StatusQuery(0), {
      wrapper: wrapper(client),
    })

    await expect.poll(() => result.current.isError).toBe(true)
  })
})

describe('useL2ApplyMutation', () => {
  it('成功后失效 l2/status、host-network 与 dhcp 详情三个缓存', async () => {
    mocks.apply.mockResolvedValue(APPLIED)
    const client = newClient()
    client.setQueryData(['l2', 'status'], STATE)
    client.setQueryData(['system', 'host-network'], { stale: true })
    client.setQueryData(['services', 'dhcp'], { stale: true })
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    const { result } = await renderHook(() => useL2ApplyMutation(), {
      wrapper: wrapper(client),
    })
    await result.current.mutateAsync({
      parent_iface: 'enp125s0f1',
      l2_subnet: '192.168.90.0/24',
      l2_gateway: '192.168.90.1',
      l2_subnet_v6: 'fd00:90::/64',
      l2_gateway_v6: 'fd00:90::1',
      sync_service_config: true,
    })

    await expect.poll(() => invalidate.mock.calls.length).toBeGreaterThan(0)
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey)
    expect(keys).toContainEqual(['l2', 'status'])
    expect(keys).toContainEqual(['system', 'host-network'])
    expect(keys).toContainEqual(['services', 'dhcp'])
  })

  it('失败时把错误交给调用方（页面据此提示）', async () => {
    mocks.apply.mockRejectedValue(new Error('400'))
    const client = newClient()

    const { result } = await renderHook(() => useL2ApplyMutation(), {
      wrapper: wrapper(client),
    })

    await expect(
      result.current.mutateAsync({
        parent_iface: '',
        l2_subnet: '192.168.90.0/24',
        l2_gateway: '192.168.90.1',
        l2_subnet_v6: '',
        l2_gateway_v6: '',
        sync_service_config: true,
      })
    ).rejects.toThrow('400')
  })
})

describe('useL2DisableMutation', () => {
  it('成功后刷新二层状态与宿主网口', async () => {
    mocks.disable.mockResolvedValue({ ...APPLIED, message: '二层夹具已停用。' })
    const client = newClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    const { result } = await renderHook(() => useL2DisableMutation(), {
      wrapper: wrapper(client),
    })
    await result.current.mutateAsync()

    await expect.poll(() => invalidate.mock.calls.length).toBeGreaterThan(0)
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey)
    expect(keys).toContainEqual(['l2', 'status'])
    expect(keys).toContainEqual(['system', 'host-network'])
  })
})
