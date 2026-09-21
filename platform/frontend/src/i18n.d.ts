import { type zh } from '@/locales/zh.json'
import 'i18next'

/**
 * 把资源类型喂给 i18next，让 t('...') / useTranslation() 的 key 在编译期受约束：
 * 拼错 key 是构建失败，而不是运行时把 'services.list.titl' 显示给用户。
 * 以中文资源为准——英文资源的 key 集合由单测保证一致（src/lib/i18n.test.ts）。
 */
declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation'
    resources: {
      translation: typeof zh
    }
  }
}
