import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { type ServicePort } from '@/api/services'
import { ServicePortList } from './service-port-list'

function port(
  containerPort: number,
  hostPort: number | null = null,
  protocol: ServicePort['protocol'] = 'tcp'
): ServicePort {
  return {
    port: containerPort,
    protocol,
    description: `${containerPort} 的描述`,
    host_port: hostPort,
  }
}

describe('ServicePortList', () => {
  it('容器端口与发布端口不同时都显示，箭头后是宿主机端口', async () => {
    // 实机 nginx：容器 80/443 发布在宿主 18102/18103，宿主 80 是别的应用
    const screen = await render(
      <ServicePortList ports={[port(80, 18102), port(443, 18103)]} />
    )

    await expect.element(screen.getByText('80/tcp → 18102')).toBeVisible()
    await expect.element(screen.getByText('443/tcp → 18103')).toBeVisible()
    await expect
      .element(screen.getByText('（箭头后为宿主机发布端口，管理网上用后者）'))
      .toBeVisible()
  })

  it('发布端口与容器端口同号时只显示一次，也不挂多余的箭头提示', async () => {
    const screen = await render(
      <ServicePortList ports={[port(514, 514, 'udp')]} />
    )

    await expect.element(screen.getByText('514/udp')).toBeVisible()
    await expect
      .element(screen.getByText('514/udp → 514'))
      .not.toBeInTheDocument()
    // 没有箭头就不该解释箭头（实机 samba 的 445/139 就是这种情形）
    await expect
      .element(screen.getByText('（箭头后为宿主机发布端口，管理网上用后者）'))
      .not.toBeInTheDocument()
  })

  it('未部署（没有发布端口）时只显示容器端口', async () => {
    const screen = await render(<ServicePortList ports={[port(8080)]} />)

    await expect.element(screen.getByText('8080/tcp')).toBeVisible()
  })

  it('绑定了二层网段时提示在网段上用容器端口', async () => {
    const screen = await render(
      <ServicePortList
        ports={[port(67, null, 'udp')]}
        l2Address='192.168.95.2'
      />
    )

    await expect
      .element(screen.getByText('（测试网段 192.168.95.2 上用这些容器端口）'))
      .toBeVisible()
  })

  it('没有端口时不渲染（不占位置）', async () => {
    const screen = await render(<ServicePortList ports={[]} />)

    await expect.element(screen.getByText('端口：')).not.toBeInTheDocument()
  })
})
