import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import i18n, { DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY } from '@/lib/i18n'
import { LanguageSwitch } from './language-switch'

beforeEach(async () => {
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

afterEach(async () => {
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

describe('LanguageSwitch', () => {
  it('默认展示中文，菜单里给出中英两个选项', async () => {
    const screen = await render(<LanguageSwitch />)

    await userEvent.click(screen.getByRole('button', { name: /语言/ }))

    await expect.element(screen.getByText('中文')).toBeInTheDocument()
    await expect.element(screen.getByText('English')).toBeInTheDocument()
  })

  it('选择 English 后立即生效并写入 localStorage，菜单文案随语言变化', async () => {
    const screen = await render(<LanguageSwitch />)

    await userEvent.click(screen.getByRole('button', { name: /语言/ }))
    await userEvent.click(screen.getByText('English'))

    // 界面立即切成英文：触发按钮的 a11y 文案与选项名都变了
    await expect
      .element(screen.getByRole('button', { name: /language/i }))
      .toBeInTheDocument()
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(i18n.t('common.signOut')).toBe('Sign out')
  })

  it('切到 English 后再选中文可切回', async () => {
    await i18n.changeLanguage('en')
    const screen = await render(<LanguageSwitch />)

    await userEvent.click(screen.getByRole('button', { name: /language/i }))
    // 选项一律用各语言自身文字（中文 / English），不随当前界面语言变化
    await userEvent.click(screen.getByText('中文'))

    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh')
    await expect
      .element(screen.getByRole('button', { name: /语言/ }))
      .toBeInTheDocument()
  })
})
