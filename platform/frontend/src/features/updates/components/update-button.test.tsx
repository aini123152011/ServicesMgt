import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type SystemInfo } from '@/api/system'
import { UpdateButton } from './update-button'

const mocks = vi.hoisted(() => ({
  isAdmin: true,
  useSystemInfoQuery: vi.fn(),
  useUpdateStatusQuery: vi.fn(),
  applyMutate: vi.fn(),
  refetch: vi.fn(),
}))

// 面板底部的「完整设置」是 router Link；本测试只关心更新按钮本身，
// 用一个普通 <a> 顶替，免得为它拉起整个 RouterProvider
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
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

vi.mock('@/hooks/use-permissions', () => ({
  usePermissions: () => ({
    isAdmin: mocks.isAdmin,
    isOperator: mocks.isAdmin,
    roles: [],
  }),
}))

vi.mock('@/features/settings/hooks/use-system', () => ({
  useSystemInfoQuery: mocks.useSystemInfoQuery,
  useUpdateStatusQuery: mocks.useUpdateStatusQuery,
  useApplyUpdateMutation: () => ({ mutate: mocks.applyMutate }),
  useApplyAllUpdatesMutation: () => ({
    mutate: mocks.applyMutate,
    isPending: false,
  }),
}))

const BASE_INFO: SystemInfo = {
  version: '0.4.9',
  build: '202609220000',
  update_registry: '',
  docker_available: true,
  status: null,
  targets: [
    {
      target: 'nginx',
      display_name: 'HTTP/HTTPS 文件服务',
      container_name: 'fx-nginx',
      image: 'bmc/nginx:latest',
      running_image_id: 'sha256:aaaa11112222',
      available_image_id: 'sha256:bbbb33334444',
      image_created: null,
      container_running: true,
      update_available: true,
    },
    {
      target: 'chrony',
      display_name: 'NTP 时间服务器',
      container_name: 'fx-chrony',
      image: 'bmc/chrony:latest',
      running_image_id: 'sha256:cccc55556666',
      available_image_id: 'sha256:cccc55556666',
      image_created: null,
      container_running: true,
      update_available: false,
    },
  ],
}

describe('UpdateButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.isAdmin = true
    mocks.useSystemInfoQuery.mockReturnValue({
      data: BASE_INFO,
      isFetching: false,
      refetch: mocks.refetch,
    })
    mocks.useUpdateStatusQuery.mockReturnValue({ data: null })
  })

  it('非管理员不渲染入口（权限在组件内自守）', async () => {
    mocks.isAdmin = false

    const screen = await render(<UpdateButton />)

    expect(screen.container.querySelector('button')).toBeNull()
  })

  it('有可用更新时按钮带圆点，面板只列出需要更新的目标', async () => {
    const screen = await render(<UpdateButton />)

    // 有可用更新：无障碍名变成带数量的提示，且带一个红点标记
    const trigger = screen.getByRole('button', { name: '有 1 个可用更新' })
    await expect.element(trigger).toBeInTheDocument()

    await userEvent.click(trigger)

    // 只列 nginx（chrony 已是最新），并展示「运行中镜像 → 可用镜像」的短 ID
    await expect
      .element(screen.getByText('HTTP/HTTPS 文件服务'))
      .toBeInTheDocument()
    expect(screen.getByText('NTP 时间服务器').query()).toBeNull()
    await expect.element(screen.getByText(/aaaa11112222/)).toBeInTheDocument()
  })

  it('点更新按钮按目标提交，并把镜像名一起带上', async () => {
    const screen = await render(<UpdateButton />)

    await userEvent.click(
      screen.getByRole('button', { name: '有 1 个可用更新' })
    )
    await userEvent.click(
      screen.getByRole('button', { name: '更新', exact: true })
    )

    expect(mocks.applyMutate).toHaveBeenCalledWith(
      { target: 'nginx', image: 'bmc/nginx:latest' },
      expect.objectContaining({ onSuccess: expect.any(Function) })
    )
  })

  it('无可用更新且没有更新任务时不渲染按钮', async () => {
    mocks.useSystemInfoQuery.mockReturnValue({
      data: {
        ...BASE_INFO,
        targets: BASE_INFO.targets.map((target) => ({
          ...target,
          update_available: false,
        })),
      },
      isFetching: false,
      refetch: mocks.refetch,
    })

    const screen = await render(<UpdateButton />)

    // 入口整个不渲染：常驻一个点开只说「已是最新」的按钮是噪音
    expect(screen.container.querySelector('button')).toBeNull()
  })

  it('Docker 不可达时说明原因，而不是显示「已是最新」', async () => {
    mocks.useSystemInfoQuery.mockReturnValue({
      data: { ...BASE_INFO, docker_available: false, targets: [] },
      isFetching: false,
      refetch: mocks.refetch,
    })
    // 无可用更新时按钮默认不渲染，借「上次任务失败」把入口留住，再验证面板内容
    mocks.useUpdateStatusQuery.mockReturnValue({
      data: { status: 'failed', target: 'nginx', message: null },
    })

    const screen = await render(<UpdateButton />)
    await userEvent.click(screen.getByRole('button', { name: '检查更新' }))

    await expect.element(screen.getByText(/Docker 不可达/)).toBeInTheDocument()
  })

  it('上次更新失败时按钮保持可见，并在面板里标出失败', async () => {
    mocks.useSystemInfoQuery.mockReturnValue({
      data: {
        ...BASE_INFO,
        targets: BASE_INFO.targets.map((target) => ({
          ...target,
          update_available: false,
        })),
      },
      isFetching: false,
      refetch: mocks.refetch,
    })
    mocks.useUpdateStatusQuery.mockReturnValue({
      data: { status: 'failed', target: 'nginx', message: null },
    })

    const screen = await render(<UpdateButton />)
    await userEvent.click(screen.getByRole('button', { name: '检查更新' }))

    // 失败要留口：否则用户看不出上次更新没成功（失败原因在「设置 → 系统更新」）
    await expect.element(screen.getByText('失败')).toBeInTheDocument()
  })

  it('更新任务执行中时禁用按钮并展示任务状态', async () => {
    mocks.useUpdateStatusQuery.mockReturnValue({
      data: { status: 'running', target: 'nginx', message: null },
    })

    const screen = await render(<UpdateButton />)
    await userEvent.click(
      screen.getByRole('button', { name: '有 1 个可用更新' })
    )

    expect(
      screen.getByRole('button', { name: '更新', exact: true }).element()
    ).toBeDisabled()
    await expect.element(screen.getByText(/执行中/)).toBeInTheDocument()
  })

  it('任务在跑时即使没有可用更新按钮也保持可见', async () => {
    mocks.useSystemInfoQuery.mockReturnValue({
      data: {
        ...BASE_INFO,
        targets: BASE_INFO.targets.map((target) => ({
          ...target,
          update_available: false,
        })),
      },
      isFetching: false,
      refetch: mocks.refetch,
    })
    mocks.useUpdateStatusQuery.mockReturnValue({
      data: { status: 'running', target: 'nginx', message: null },
    })

    const screen = await render(<UpdateButton />)
    // 更新进行中必须留口，否则用户看不到任务在跑
    await userEvent.click(screen.getByRole('button', { name: '检查更新' }))

    await expect
      .element(screen.getByText('所有目标都是最新版本'))
      .toBeInTheDocument()
    await expect.element(screen.getByText(/执行中/)).toBeInTheDocument()
  })
})
