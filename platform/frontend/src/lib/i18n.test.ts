import en from '@/locales/en.json'
import zh from '@/locales/zh.json'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n, {
  DEFAULT_LANGUAGE,
  LANGUAGE_STORAGE_KEY,
  flattenKeys,
  getStoredLanguage,
  setLanguage,
} from './i18n'

/** 语言是全局单例状态：每条用例前后都复位，避免互相污染 */
beforeEach(async () => {
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

afterEach(async () => {
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

describe('i18n 资源', () => {
  it('中英 key 集合完全一致（防止漏翻导致回退中文或 key 外露）', () => {
    const zhKeys = flattenKeys(zh).sort()
    const enKeys = flattenKeys(en).sort()

    // 分开断言：差集能直接指出漏了哪个 key，而不是只报"长度不等"
    expect(enKeys.filter((key) => !zhKeys.includes(key))).toEqual([])
    expect(zhKeys.filter((key) => !enKeys.includes(key))).toEqual([])
    expect(enKeys).toEqual(zhKeys)
  })

  it('覆盖 design.md 约定的命名空间', () => {
    // 用顶层分组判定：分组本身始终存在，即使其中暂时没有 key（拍平后取不到）
    expect(Object.keys(zh).sort()).toEqual(
      [
        'auth',
        'common',
        'errors',
        'home',
        'nav',
        'services',
        'settings',
        'system',
        'ui',
        'users',
      ].sort()
    )
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort())
  })

  it('默认语言是中文，且未选择语言时解析到中文', () => {
    expect(DEFAULT_LANGUAGE).toBe('zh')
    expect(getStoredLanguage()).toBe('zh')
    expect(i18n.t('common.cancel')).toBe('取消')
  })

  it('未知语言值回落中文，不把界面留在没有资源的语言上', () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'fr')
    expect(getStoredLanguage()).toBe('zh')
  })
})

describe('语言切换', () => {
  it('切换语言写入 localStorage 并同步 <html lang>', async () => {
    await setLanguage('en')

    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(i18n.t('common.cancel')).toBe('Cancel')
  })

  it('切回中文同样持久化', async () => {
    await setLanguage('en')
    await setLanguage('zh')

    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh')
    expect(document.documentElement.lang).toBe('zh')
    expect(i18n.t('common.cancel')).toBe('取消')
  })

  it('已持久化的语言在初始化时被读取（刷新后保持）', async () => {
    await setLanguage('en')

    // 模拟刷新：初始化时读到的就是持久化值
    expect(getStoredLanguage()).toBe('en')
    expect(i18n.language).toBe('en')
  })
})
