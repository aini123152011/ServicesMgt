import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type ServiceUsageEntry } from '@/api/services'
import { ServiceUsageCard } from './service-usage-card'

const writeText = vi.fn()

beforeEach(() => {
  writeText.mockReset()
  writeText.mockResolvedValue(undefined)
  // 真实浏览器写剪贴板需要用户授权，这里只验证「把正确的文本交给了剪贴板 API」
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
})

const BMC_ENTRY: ServiceUsageEntry = {
  target: 'BMC',
  summary: '把 BMC 的 NTP 主服务器指向本服务',
  command: 'server {{host}} iburst',
}

// 故意带结尾换行：YAML 的 `command: |` 块标量必然产出它，渲染时要裁掉
const LINUX_ENTRY: ServiceUsageEntry = {
  target: 'Linux',
  summary: '发一条测试日志',
  command: 'logger -n {{host}} -P {{port}} -d "bmc-syslog-test"\n',
}

describe('ServiceUsageCard', () => {
  it('逐条渲染使用方、说明与替换后的命令', async () => {
    const screen = await render(
      <ServiceUsageCard entries={[BMC_ENTRY, LINUX_ENTRY]} port={514} />
    )

    // exact：使用方标签 "BMC" 会与说明、命令里的同名子串一起被匹配到
    await expect
      .element(screen.getByText('BMC', { exact: true }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByText('把 BMC 的 NTP 主服务器指向本服务'))
      .toBeInTheDocument()
    await expect
      .element(screen.getByText(`server ${window.location.hostname} iburst`))
      .toBeInTheDocument()
    await expect
      .element(
        screen.getByText(
          `logger -n ${window.location.hostname} -P 514 -d "bmc-syslog-test"`
        )
      )
      .toBeInTheDocument()
  })

  it('没有 usage（未声明或空列表）时不渲染卡片', async () => {
    const empty = await render(<ServiceUsageCard entries={[]} port={514} />)
    await expect
      .element(empty.getByText('外部使用方式'))
      .not.toBeInTheDocument()

    const missing = await render(<ServiceUsageCard />)
    await expect
      .element(missing.getByText('外部使用方式'))
      .not.toBeInTheDocument()
  })

  it('端口未知时保留 {{port}} 原文，不猜一个端口填进去', async () => {
    const screen = await render(<ServiceUsageCard entries={[LINUX_ENTRY]} />)

    await expect
      .element(
        screen.getByText(
          `logger -n ${window.location.hostname} -P {{port}} -d "bmc-syslog-test"`
        )
      )
      .toBeInTheDocument()
  })

  it('复制按钮把替换后的命令写入剪贴板', async () => {
    const screen = await render(
      <ServiceUsageCard entries={[BMC_ENTRY]} port={123} />
    )

    await userEvent.click(screen.getByRole('button', { name: '复制' }))

    expect(writeText).toHaveBeenCalledWith(
      `server ${window.location.hostname} iburst`
    )
  })
})

it('绑定了二层网段时用测试网段地址替换 {{host}}，而不是访问地址', async () => {
  const entries: ServiceUsageEntry[] = [
    { target: 'BMC', summary: '取址', command: 'dig @{{host}} bmc-01.bmc.lab' },
  ]
  const screen = await render(
    <ServiceUsageCard entries={entries} port={53} l2Address='192.168.90.1' />
  )

  // 被测 BMC 在测试网段上够不到管理网地址，卡片必须显示二层地址
  await expect
    .element(screen.getByText('dig @192.168.90.1 bmc-01.bmc.lab'))
    .toBeVisible()
})

it('未绑定二层时仍用访问地址替换 {{host}}', async () => {
  const entries: ServiceUsageEntry[] = [
    {
      target: 'Linux',
      summary: '直连',
      command: 'dig @{{host}} bmc-01.bmc.lab',
    },
  ]
  const screen = await render(
    <ServiceUsageCard entries={entries} port={53} l2Address={null} />
  )

  await expect
    .element(
      screen.getByText(`dig @${window.location.hostname} bmc-01.bmc.lab`)
    )
    .toBeVisible()
})
