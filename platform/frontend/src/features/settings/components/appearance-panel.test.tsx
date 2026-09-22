import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { getCookie } from '@/lib/cookies'
import i18n, { DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY } from '@/lib/i18n'
import { LayoutProvider } from '@/context/layout-provider'
import { ThemeProvider } from '@/context/theme-provider'
import { SidebarProvider } from '@/components/ui/sidebar'
import { AppearancePanel } from './appearance-panel'

// 主题/侧边栏/布局三个分节需要布局、主题与侧边栏 Provider
function renderAppearancePanel() {
  return render(
    <ThemeProvider>
      <LayoutProvider>
        <SidebarProvider>
          <AppearancePanel />
        </SidebarProvider>
      </LayoutProvider>
    </ThemeProvider>
  )
}

describe('AppearancePanel', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
    await i18n.changeLanguage(DEFAULT_LANGUAGE)
  })

  afterEach(async () => {
    window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
    await i18n.changeLanguage(DEFAULT_LANGUAGE)
  })

  it('渲染主题/侧边栏/布局分节，并给出语言选择', async () => {
    const screen = await renderAppearancePanel()

    // 三个分节沿用抽屉的组件（标题与 aria-label 都是 ui.theme.* 的文案），
    // 用单选组名断言可避免与选项标签文本重名
    await expect
      .element(screen.getByRole('radiogroup', { name: '选择主题偏好' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('radiogroup', { name: '选择侧边栏样式' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('radiogroup', { name: '选择布局样式' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByText('语言', { exact: true }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: '中文' }))
      .toBeInTheDocument()
    await expect
      .element(screen.getByRole('button', { name: 'English' }))
      .toBeInTheDocument()
  })

  it('「全部恢复默认」把主题、侧边栏与布局一起复位', async () => {
    const screen = await renderAppearancePanel()

    await userEvent.click(screen.getByRole('radio', { name: /^选择深色$/ }))
    await userEvent.click(screen.getByRole('radio', { name: /^选择悬浮$/ }))
    await userEvent.click(screen.getByRole('radio', { name: /^选择通栏$/ }))

    await vi.waitFor(() => expect(getCookie('vite-ui-theme')).toBe('dark'))
    await vi.waitFor(() => expect(getCookie('layout_variant')).toBe('floating'))
    await vi.waitFor(() =>
      expect(getCookie('layout_collapsible')).toBe('offcanvas')
    )

    await userEvent.click(screen.getByRole('button', { name: '全部恢复默认' }))

    // 配色复位 = 删除 cookie，回到默认的「跟随系统」
    await vi.waitFor(() => expect(getCookie('vite-ui-theme')).toBeUndefined())
    await vi.waitFor(() => expect(getCookie('layout_variant')).toBe('inset'))
    await vi.waitFor(() => expect(getCookie('layout_collapsible')).toBe('icon'))
  })

  it('点语言按钮即切换界面语言并持久化', async () => {
    const screen = await renderAppearancePanel()

    await userEvent.click(screen.getByRole('button', { name: 'English' }))

    await vi.waitFor(() => {
      expect(i18n.language).toBe('en')
      expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en')
    })
  })
})
