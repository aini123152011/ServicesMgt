import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@/lib/i18n'
import { serviceDisplayName } from './service-name'

const SERVICE = {
  display_name: 'NTP 时间服务器',
  display_name_en: 'NTP Time Server',
}

beforeEach(async () => {
  await i18n.changeLanguage('zh')
})

describe('serviceDisplayName', () => {
  it('中文界面用 display_name', () => {
    expect(serviceDisplayName(SERVICE)).toBe('NTP 时间服务器')
  })

  it('英文界面优先用 display_name_en', async () => {
    await i18n.changeLanguage('en')
    expect(serviceDisplayName(SERVICE)).toBe('NTP Time Server')
  })

  it('英文界面但缺 display_name_en 时回落中文名（不显示空）', async () => {
    await i18n.changeLanguage('en')
    expect(serviceDisplayName({ ...SERVICE, display_name_en: null })).toBe(
      'NTP 时间服务器'
    )
  })

  it('en-US 这类带地区的语言码也算英文', async () => {
    await i18n.changeLanguage('en-US')
    expect(serviceDisplayName(SERVICE)).toBe('NTP Time Server')
  })
})
