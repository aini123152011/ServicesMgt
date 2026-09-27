import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { copyText } from './clipboard'

/**
 * 平台的访问方式本身就是 `http://<宿主IP>:18080`（非安全上下文），
 * 那里 `navigator.clipboard` 是 undefined——这几条用例锁住「有 API 用它、没有就降级」。
 */
describe('copyText', () => {
  const writeText = vi.fn()
  const execCommand = vi.fn()

  function setClipboard(value: unknown) {
    Object.defineProperty(navigator, 'clipboard', {
      value,
      configurable: true,
    })
  }

  beforeEach(() => {
    writeText.mockReset()
    execCommand.mockReset()
    execCommand.mockReturnValue(true)
    document.execCommand = execCommand
  })

  afterEach(() => {
    setClipboard(undefined)
  })

  it('安全上下文里用异步 Clipboard API', async () => {
    writeText.mockResolvedValue(undefined)
    setClipboard({ writeText })

    expect(await copyText('curl -I http://x/')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('curl -I http://x/')
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('非安全上下文（navigator.clipboard 不存在）走 execCommand 降级', async () => {
    setClipboard(undefined)

    expect(await copyText('server 10.0.0.1 iburst')).toBe(true)
    expect(execCommand).toHaveBeenCalledWith('copy')
    // 降级路径用临时 textarea 承载文本，拷完必须移除，否则页面里留垃圾节点
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('API 存在但被拒时也降级，而不是直接报失败', async () => {
    writeText.mockRejectedValue(new DOMException('NotAllowedError'))
    setClipboard({ writeText })

    expect(await copyText('text')).toBe(true)
    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it('两条路径都不行时返回 false，让调用方报失败而不是假成功', async () => {
    setClipboard(undefined)
    execCommand.mockReturnValue(false)

    expect(await copyText('text')).toBe(false)
  })

  it('execCommand 抛异常时不炸，返回 false 且清掉临时节点', async () => {
    setClipboard(undefined)
    execCommand.mockImplementation(() => {
      throw new Error('copy is not supported')
    })

    expect(await copyText('text')).toBe(false)
    expect(document.querySelector('textarea')).toBeNull()
  })
})
