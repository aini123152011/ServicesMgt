import { clearCookies } from '@/test-utils/cookies'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { getCookie, setCookie } from '@/lib/cookies'
import i18n, { DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY } from '@/lib/i18n'
import { LayoutProvider } from '@/context/layout-provider'
import { ThemeProvider } from '@/context/theme-provider'
import { SidebarProvider } from '@/components/ui/sidebar'
import { LayoutConfig, SidebarConfig, ThemeConfig } from './appearance-config'

// 分节文案已随界面语言切换，这里的断言针对默认语言（中文）——与线上首屏一致

/** 直接渲染三个配置分节：抽屉已移除，这些分节现在由「设置 → 外观」页渲染 */
async function renderSections({
  sidebarDefaultOpen = true,
}: {
  sidebarDefaultOpen?: boolean
} = {}) {
  return await render(
    <ThemeProvider>
      <LayoutProvider>
        <SidebarProvider defaultOpen={sidebarDefaultOpen}>
          <ThemeConfig />
          <SidebarConfig />
          <LayoutConfig />
        </SidebarProvider>
      </LayoutProvider>
    </ThemeProvider>
  )
}

describe('AppearanceConfig (integration)', () => {
  beforeEach(async () => {
    vi.clearAllMocks()

    clearCookies()

    // 语言是全局单例状态：显式复位，避免同文件/同浏览器上下文里串味
    window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
    await i18n.changeLanguage(DEFAULT_LANGUAGE)

    document.documentElement.classList.remove('light', 'dark')
    document.documentElement.removeAttribute('dir')
  })

  it('renders the three config sections', async () => {
    const screen = await renderSections()

    await expect.element(screen.getByText(/^配色$/)).toBeInTheDocument()
    await expect.element(screen.getByText(/^布局$/)).toBeInTheDocument()
    await expect
      .element(screen.getByText(/^侧边栏$/).first())
      .toBeInTheDocument()
  })

  describe('theme preference', () => {
    it('applies light theme to <html> and cookie', async () => {
      const screen = await renderSections()
      await userEvent.click(screen.getByRole('radio', { name: /^选择浅色$/ }))
      await vi.waitFor(() =>
        expect(document.documentElement.classList.contains('light')).toBe(true)
      )
      expect(getCookie('vite-ui-theme')).toBe('light')
    })

    it('applies dark theme to <html> and cookie', async () => {
      const screen = await renderSections()
      await userEvent.click(screen.getByRole('radio', { name: /^选择深色$/ }))
      await vi.waitFor(() =>
        expect(document.documentElement.classList.contains('dark')).toBe(true)
      )
      expect(getCookie('vite-ui-theme')).toBe('dark')
    })

    it('applies system theme: stores cookie and applies a resolved light or dark class', async () => {
      // Pre-seed light so mounted theme is not system; re-selecting System alone would not fire setTheme.
      setCookie('vite-ui-theme', 'light')

      const screen = await renderSections()

      await userEvent.click(
        screen.getByRole('radio', { name: /^选择跟随系统$/ })
      )
      await vi.waitFor(() => expect(getCookie('vite-ui-theme')).toBe('system'))
      await vi.waitFor(() => {
        const root = document.documentElement
        const hasLight = root.classList.contains('light')
        const hasDark = root.classList.contains('dark')
        expect(hasLight !== hasDark).toBe(true)
      })
    })
  })

  describe('sidebar variant', () => {
    it('selecting floating updates layout_variant cookie', async () => {
      const screen = await renderSections()

      await userEvent.click(screen.getByRole('radio', { name: /^选择悬浮$/ }))
      await vi.waitFor(() =>
        expect(getCookie('layout_variant')).toBe('floating')
      )
    })

    it('selecting sidebar updates layout_variant cookie', async () => {
      const screen = await renderSections()

      await userEvent.click(screen.getByRole('radio', { name: /^选择侧边栏$/ }))
      await vi.waitFor(() =>
        expect(getCookie('layout_variant')).toBe('sidebar')
      )
    })

    it('selecting inset updates layout_variant cookie after another variant', async () => {
      const screen = await renderSections()

      await userEvent.click(screen.getByRole('radio', { name: /^选择悬浮$/ }))
      await vi.waitFor(() =>
        expect(getCookie('layout_variant')).toBe('floating')
      )

      await userEvent.click(screen.getByRole('radio', { name: /^选择内嵌$/ }))
      await vi.waitFor(() => expect(getCookie('layout_variant')).toBe('inset'))
    })
  })

  it('selecting full layout sets collapsible to offcanvas and closes sidebar', async () => {
    const screen = await renderSections({ sidebarDefaultOpen: true })

    await userEvent.click(screen.getByRole('radio', { name: /^选择通栏$/ }))
    await vi.waitFor(() =>
      expect(getCookie('layout_collapsible')).toBe('offcanvas')
    )
    await vi.waitFor(() => expect(getCookie('sidebar_state')).toBe('false'))
  })

  describe('section reset buttons', () => {
    it('resets theme via section control after choosing dark', async () => {
      const screen = await renderSections()

      await userEvent.click(screen.getByRole('radio', { name: /^选择深色$/ }))
      await vi.waitFor(() => expect(getCookie('vite-ui-theme')).toBe('dark'))

      await userEvent.click(
        screen.getByRole('button', {
          name: /^恢复默认配色$/,
        })
      )
      await vi.waitFor(() => expect(getCookie('vite-ui-theme')).toBe('system'))
    })

    it('resets sidebar style via section control after choosing floating', async () => {
      const screen = await renderSections()

      await userEvent.click(screen.getByRole('radio', { name: /^选择悬浮$/ }))
      await vi.waitFor(() =>
        expect(getCookie('layout_variant')).toBe('floating')
      )

      await userEvent.click(
        screen.getByRole('button', {
          name: /^恢复默认侧边栏$/,
        })
      )
      await vi.waitFor(() => expect(getCookie('layout_variant')).toBe('inset'))
    })

    it('resets layout via section control after choosing compact', async () => {
      const screen = await renderSections({ sidebarDefaultOpen: true })

      await userEvent.click(screen.getByRole('radio', { name: /^选择紧凑$/ }))
      await vi.waitFor(() => expect(getCookie('sidebar_state')).toBe('false'))

      await userEvent.click(
        screen.getByRole('button', {
          name: /^恢复默认布局$/,
        })
      )
      await vi.waitFor(() => expect(getCookie('sidebar_state')).toBe('true'))
      await vi.waitFor(() =>
        expect(getCookie('layout_collapsible')).toBe('icon')
      )
    })
  })

  it('updates layout: selecting non-default closes sidebar and changes layout cookie', async () => {
    const screen = await renderSections({ sidebarDefaultOpen: true })

    await expect
      .element(screen.getByRole('radio', { name: /^选择默认$/ }))
      .toHaveAttribute('data-state', 'checked')

    await userEvent.click(screen.getByRole('radio', { name: /^选择紧凑$/ }))

    await vi.waitFor(() => expect(getCookie('sidebar_state')).toBe('false'))
    await vi.waitFor(() => expect(getCookie('layout_collapsible')).toBe('icon'))
  })

  it('切到英文后分节文案整体变英文（中英双向都有词条）', async () => {
    await i18n.changeLanguage('en')

    const screen = await renderSections()

    await expect.element(screen.getByText(/^Theme$/)).toBeInTheDocument()
    await expect.element(screen.getByText(/^Layout$/)).toBeInTheDocument()
    await expect
      .element(screen.getByRole('radio', { name: /^Select Dark$/ }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('radio', { name: /^Select Floating$/ }))
      .toBeInTheDocument()
  })
})
