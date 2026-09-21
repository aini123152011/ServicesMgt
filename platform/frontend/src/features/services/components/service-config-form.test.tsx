import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type ServiceConfig, type ServiceField } from '@/api/services'
import { ServiceConfigForm, type ConfigTab } from './service-config-form'

vi.mock('../hooks/use-services', () => ({
  useUpdateConfigMutation: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@/hooks/use-permissions', () => ({
  usePermissions: () => ({ isAdmin: true, isOperator: true, roles: ['admin'] }),
}))

const BASE_FIELD: ServiceField = {
  name: 'port',
  label: '基准端口字段',
  type: 'integer',
  required: true,
  default: 514,
}

const FAULT_FIELD: ServiceField = {
  name: 'fault_mode',
  label: '故障模式字段',
  type: 'enum',
  group: 'fault',
  options: ['none', 'drop_all'],
  default: 'none',
}

const CONFIG: ServiceConfig = {
  values: {},
  applied: true,
  rendered_at: '2026-09-21T00:00:00Z',
}

/** 受控页签：值由父组件给，保存触发重挂载时父组件状态不受影响 */
function ControlledForm({
  activeTab,
  onTabChange,
  formKey,
}: {
  activeTab: ConfigTab
  onTabChange: (tab: ConfigTab) => void
  formKey: string
}) {
  return (
    <ServiceConfigForm
      key={formKey}
      name='rsyslog'
      fields={[BASE_FIELD, FAULT_FIELD]}
      config={CONFIG}
      activeTab={activeTab}
      onTabChange={onTabChange}
    />
  )
}

describe('ServiceConfigForm 页签', () => {
  it('按 activeTab 渲染对应分组', async () => {
    const screen = await render(
      <ControlledForm activeTab='base' onTabChange={vi.fn()} formKey='a' />
    )

    await expect.element(screen.getByText('基准端口字段')).toBeInTheDocument()
    await expect
      .element(screen.getByText('故障模式字段'))
      .not.toBeInTheDocument()
  })

  it('点击页签把新值上报给父组件（受控）', async () => {
    const onTabChange = vi.fn()
    const screen = await render(
      <ControlledForm activeTab='base' onTabChange={onTabChange} formKey='a' />
    )

    await userEvent.click(screen.getByRole('tab', { name: /故障注入/ }))

    expect(onTabChange).toHaveBeenCalledWith('fault')
  })

  it('重挂载（保存后 config.rendered_at 变化触发）仍停在当前页签', async () => {
    const screen = await render(
      <ControlledForm activeTab='fault' onTabChange={vi.fn()} formKey='a' />
    )
    await expect.element(screen.getByText('故障模式字段')).toBeInTheDocument()

    // 模拟保存成功：表单 key 变化导致重挂载，父组件状态不变
    await screen.rerender(
      <ControlledForm activeTab='fault' onTabChange={vi.fn()} formKey='b' />
    )

    await expect.element(screen.getByText('故障模式字段')).toBeInTheDocument()
    await expect
      .element(screen.getByText('基准端口字段'))
      .not.toBeInTheDocument()
  })
})
