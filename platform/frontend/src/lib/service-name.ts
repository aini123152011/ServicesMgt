import { type ServiceSummary } from '@/api/services'
import i18n from '@/lib/i18n'

/**
 * 服务的展示名：英文界面优先用 `display_name_en`，缺失时回落中文 `display_name`。
 *
 * 英文元数据目前只覆盖「服务名」这一层；字段 label/help 仍是中文（沿用 `_en` 约定，后续按需补）。
 */
export function serviceDisplayName(
  service: Pick<ServiceSummary, 'display_name' | 'display_name_en'>
): string {
  const isEnglish = i18n.language?.startsWith('en') ?? false
  return isEnglish && service.display_name_en
    ? service.display_name_en
    : service.display_name
}
