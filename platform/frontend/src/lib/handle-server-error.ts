import { AxiosError } from 'axios'
import { toast } from 'sonner'

export function handleServerError(error: unknown) {
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(error)
  }

  let errMsg = 'Something went wrong!'

  if (
    error &&
    typeof error === 'object' &&
    'status' in error &&
    Number(error.status) === 204
  ) {
    errMsg = 'No content.'
  }

  if (error instanceof AxiosError) {
    // FastAPI 错误统一带 { detail }，优先展示给用户
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail.length > 0) {
      errMsg = detail
    } else if (detail != null) {
      // 422 校验错误的 detail 是数组，转成文本避免丢失信息
      errMsg = JSON.stringify(detail)
    } else {
      const title = error.response?.data?.title
      if (typeof title === 'string' && title.length > 0) {
        errMsg = title
      }
    }
  }

  toast.error(errMsg)
}
