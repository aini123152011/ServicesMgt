import { AxiosError } from 'axios'
import { toast } from 'sonner'
import i18n, { type TranslationKey } from '@/lib/i18n'

/**
 * 常见状态码的本地化通用文案。
 *
 * 后端 detail 与审计动作名保持中文（服务端文案，审计可读性优先），
 * 英文界面下若直接展示 detail 会出现整段中文；这几个高频状态码改用前端本地化文案。
 * 未列出的状态码（如 400 登录失败、422 校验错误）仍原样展示后端 detail——那些文案
 * 带具体信息（哪个字段、为什么），比通用文案有用。
 */
const statusMessageKeys: Record<number, TranslationKey> = {
  401: 'errors.status401',
  403: 'errors.status403',
  404: 'errors.status404',
  409: 'errors.status409',
  502: 'errors.status502',
}

/** 错误 → 展示文案：状态码本地化文案 > 后端 detail/title > 通用兜底 */
export function resolveErrorMessage(error: unknown): string {
  if (
    error &&
    typeof error === 'object' &&
    'status' in error &&
    Number(error.status) === 204
  ) {
    return i18n.t('errors.noContent')
  }

  if (error instanceof AxiosError) {
    const status = error.response?.status
    const localizedKey =
      status === undefined ? undefined : statusMessageKeys[status]
    if (localizedKey) return i18n.t(localizedKey)

    // FastAPI 错误统一带 { detail }，优先展示给用户
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail.length > 0) {
      return detail
    }
    if (detail != null) {
      // 422 校验错误的 detail 是数组，转成文本避免丢失信息
      return JSON.stringify(detail)
    }
    const title = error.response?.data?.title
    if (typeof title === 'string' && title.length > 0) {
      return title
    }
  }

  return i18n.t('errors.generic')
}

export function handleServerError(error: unknown) {
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(error)
  }

  toast.error(resolveErrorMessage(error))
}
