import { type ServiceSummary } from '@/api/services'
import { type NavItem } from '@/components/layout/types'
import { categoryIcons, categoryLabelKeys, categoryOrder } from './data'

/**
 * 把服务按分类整理成侧边栏菜单项：每个分类一个可折叠项，其下是该服务分类的服务。
 *
 * 分类顺序取自 categoryOrder（与 schema 的 category 枚举一致），分类内按展示名排序，
 * 保证菜单稳定——服务列表是异步拉取的，顺序若随响应变化会让人找不到入口。
 * 空分类不渲染，避免出现点开什么都没有的菜单。
 *
 * 分类名是平台自有文案，存 key 由渲染处翻译；服务展示名来自 manifest，本轮保持原文。
 */
export function buildServiceNavItems(services: ServiceSummary[]): NavItem[] {
  const items: NavItem[] = []
  for (const category of categoryOrder) {
    const children = services
      .filter((service) => service.category === category)
      .sort((a, b) => a.display_name.localeCompare(b.display_name, 'zh'))
      .map((service) => ({
        title: service.display_name,
        url: `/services/${service.name}`,
      }))
    if (children.length === 0) continue
    items.push({
      titleKey: categoryLabelKeys[category],
      icon: categoryIcons[category],
      items: children,
    })
  }
  return items
}
