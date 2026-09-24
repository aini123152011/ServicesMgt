import { useQuery } from '@tanstack/react-query'
import { type CaptchaScope, fetchCaptcha } from '@/api/auth'

/**
 * 取一张图片验证码。
 *
 * 用 TanStack Query 而不是在组件里写 effect + fetch：项目约定数据获取统一走 query，
 * 而且 effect 里同步 setState 会被 react-hooks/set-state-in-effect 拦下。
 *
 * **换图靠 refreshToken 而不是 refetch**：refreshToken 进 queryKey，递增即换 key → 自动取新图。
 * 调用方因此不需要额外维护「图片」状态，票据 id 直接从返回值读。
 *
 * 缓存策略刻意收紧：验证码是一次性易变数据，复用旧缓存会让用户提交一张已被消费的票据
 * （表现为「验证码错误」但图看着没错），所以 staleTime/gcTime 都置 0，且不在窗口聚焦时重取
 * （那会悄悄换掉用户正在看的图）。
 */
export function useCaptcha(scope: CaptchaScope, refreshToken: number) {
  const query = useQuery({
    queryKey: ['captcha', scope, refreshToken],
    queryFn: () => fetchCaptcha(scope),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  })

  return {
    image: query.data?.image ?? '',
    /** 服务端票据 id；提交时必须原样带回。取图失败时为空串，提交会被判「验证码错误」 */
    captchaId: query.data?.captcha_id ?? '',
    isLoading: query.isPending,
    isError: query.isError,
  }
}
