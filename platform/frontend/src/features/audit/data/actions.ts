import { type TranslationKey } from '@/lib/i18n'

/**
 * 审计动作名的展示文案。
 *
 * key 是后端写入的 action 原文（如 `config.update`），此处只做「翻译」，不改写原值——
 * 筛选下拉的取值必须与库里存的一致。字典里没有的动作（后端新增但前端未跟进）按
 * 原文展示，保证不会出现空白单元格。
 */
const actionLabelKeys: Record<string, TranslationKey> = {
  'config.update': 'audit.action.configUpdate',
  'config.rollback': 'audit.action.configRollback',
  'service.start': 'audit.action.serviceStart',
  'service.stop': 'audit.action.serviceStop',
  'service.restart': 'audit.action.serviceRestart',
  'user.create': 'audit.action.userCreate',
  'user.update': 'audit.action.userUpdate',
  'user.delete': 'audit.action.userDelete',
  'system.update': 'audit.action.systemUpdate',
}

/** 动作名对应的文案 key；未收录时返回 null，由调用方回退到原文 */
export function actionLabelKey(action: string): TranslationKey | null {
  return actionLabelKeys[action] ?? null
}

/** 动作分组，用于给徽章上色：服务变更、用户管理、平台更新三类一眼可分 */
type ActionKind = 'service' | 'user' | 'system' | 'other'

export function actionKind(action: string): ActionKind {
  // 配置类动作统一按 service 上色：新增 config.* 动作时不必再逐个补判断
  if (action.startsWith('service.') || action.startsWith('config.'))
    return 'service'
  if (action.startsWith('user.')) return 'user'
  if (action.startsWith('system.')) return 'system'
  return 'other'
}

/** 徽章样式：与服务的分类徽章同一套写法（浅底 + 深字 + 边框），深色模式各自配一版 */
export const actionKindClassNames: Record<ActionKind, string> = {
  service: 'bg-sky-100/40 text-sky-900 dark:text-sky-100 border-sky-300',
  user: 'bg-violet-100/40 text-violet-900 dark:text-violet-100 border-violet-300',
  system: 'bg-amber-100/40 text-amber-900 dark:text-amber-100 border-amber-300',
  other: '',
}
