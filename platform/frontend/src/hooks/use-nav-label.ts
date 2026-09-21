import { useTranslation } from 'react-i18next'
import { type TranslationKey } from '@/lib/i18n'

type NavLabelSource = {
  /** 平台自有导航文案的 i18n key */
  titleKey?: TranslationKey
  /** 直接展示的文案（服务元数据等本轮不翻译的内容） */
  title?: string
}

/**
 * 导航标题的取值函数。
 *
 * 导航配置是模块级常量，不能在那里调 t（那时拿到的是创建时的语言，切换后不会更新），
 * 所以平台文案存 key、服务元数据存原文，统一在这里按当前语言取值。
 */
export function useNavLabel() {
  const { t } = useTranslation()
  return (item: NavLabelSource): string =>
    item.titleKey ? t(item.titleKey) : (item.title ?? '')
}
