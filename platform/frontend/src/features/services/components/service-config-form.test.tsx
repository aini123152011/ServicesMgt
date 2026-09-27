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

// 明文只能按需向专用接口要（详情里是掩码）：这里替身返回明文，顺便断言调用参数
const getServiceSecret = vi.fn()
vi.mock('@/api/services', () => ({
  getServiceSecret: (name: string, field: string) =>
    getServiceSecret(name, field) as Promise<string>,
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

describe('ServiceConfigForm 敏感字段显示明文', () => {
  const SECRET_PASSWORD_FIELD: ServiceField = {
    name: 'auth_basic_password',
    label: 'Basic 认证密码',
    type: 'string',
    secret: true,
    default: '',
  }

  const SECRET_TEXT_FIELD: ServiceField = {
    name: 'ssl_key_pem',
    label: 'SSL/TLS 私钥内容',
    type: 'text',
    secret: true,
    pem: true,
    default: '',
  }

  /** 已保存过配置的服务，详情接口给的是掩码占位符 */
  const MASKED_CONFIG: ServiceConfig = {
    values: { auth_basic_password: '********', ssl_key_pem: '********' },
    applied: true,
    rendered_at: '2026-09-27T00:00:00Z',
  }

  function renderWithSecret(fields: ServiceField[]) {
    return render(
      <ServiceConfigForm
        name='nginx'
        fields={fields}
        config={MASKED_CONFIG}
        activeTab='base'
        onTabChange={vi.fn()}
      />
    )
  }

  it('点眼睛按钮取回明文并显示在原输入框里', async () => {
    getServiceSecret.mockReset()
    getServiceSecret.mockResolvedValue('bmc-fixture-pass')
    const screen = await renderWithSecret([SECRET_PASSWORD_FIELD])

    // 初始是掩码（密码框里看到的是掩码本身，不是 CSS 遮蔽）
    await expect
      .element(screen.getByLabelText('Basic 认证密码'))
      .toHaveValue('********')

    await userEvent.click(screen.getByRole('button', { name: '显示密码' }))

    await expect
      .element(screen.getByLabelText('Basic 认证密码'))
      .toHaveValue('bmc-fixture-pass')
    expect(getServiceSecret).toHaveBeenCalledWith(
      'nginx',
      'auth_basic_password'
    )
  })

  it('多行私钥点「显示内容」同样取回明文', async () => {
    getServiceSecret.mockReset()
    getServiceSecret.mockResolvedValue('-----BEGIN PRIVATE KEY-----\nabc\n')
    const screen = await renderWithSecret([SECRET_TEXT_FIELD])

    await userEvent.click(screen.getByRole('button', { name: '显示内容' }))

    await expect
      .element(screen.getByRole('textbox'))
      .toHaveValue('-----BEGIN PRIVATE KEY-----\nabc\n')
    expect(getServiceSecret).toHaveBeenCalledWith('nginx', 'ssl_key_pem')
  })

  it('取明文失败时报错且不回退成显示掩码', async () => {
    getServiceSecret.mockReset()
    getServiceSecret.mockRejectedValue(new Error('403'))
    const screen = await renderWithSecret([SECRET_PASSWORD_FIELD])

    await userEvent.click(screen.getByRole('button', { name: '显示密码' }))

    await expect
      .element(screen.getByLabelText('Basic 认证密码'))
      .toHaveValue('********')
  })
})
