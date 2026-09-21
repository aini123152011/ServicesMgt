import { type TranslationKey } from '@/lib/i18n'

/** 后端可能返回的更新任务状态；未列出的值一律按 idle 显示 */
const TASK_STATUS_KEYS: Record<string, TranslationKey> = {
  idle: 'system.status.idle',
  running: 'system.status.running',
  succeeded: 'system.status.succeeded',
  failed: 'system.status.failed',
}

/**
 * 更新任务状态 → i18n key。
 *
 * 后端字段是字符串（可能是新增状态或 null），这里收窄到已知集合再取 key，
 * 未知值回落 idle——直接把状态名拼进文案会把英文状态或 key 露到界面上。
 * 更新面板与「关于」页都要显示该状态，故放在共享数据模块里而不是各自实现。
 */
export function taskStatusKey(
  status: string | null | undefined
): TranslationKey {
  if (status && status in TASK_STATUS_KEYS) {
    return TASK_STATUS_KEYS[status]
  }
  return 'system.status.idle'
}
