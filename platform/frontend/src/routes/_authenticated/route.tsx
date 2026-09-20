import { isAxiosError } from 'axios'
import { createFileRoute, redirect } from '@tanstack/react-router'
import { getCurrentUser } from '@/api/auth'
import { useAuthStore } from '@/stores/auth-store'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'

export const Route = createFileRoute('/_authenticated')({
  component: AuthenticatedLayout,
  beforeLoad: async ({ location }) => {
    const store = useAuthStore.getState()
    const backToSignIn = () =>
      redirect({
        to: '/sign-in',
        search: { redirect: `${location.pathname}${location.searchStr}` },
        replace: true,
      })

    // 未登录挡在布局加载之前，并记录来源路径便于登录后回跳
    if (!store.auth.accessToken) {
      throw backToSignIn()
    }

    // user 不持久化（仅登录时写入），刷新后用 token 重新水合，
    // 否则 roles 为空会导致侧边栏与操作按钮的权限判定塌缩成只读
    if (!store.auth.user) {
      try {
        store.auth.setUser(await getCurrentUser())
      } catch (error) {
        // 仅凭证失效才清凭证回登录页；其余错误（网络抖动等）交给路由错误组件
        if (
          isAxiosError(error) &&
          (error.status === 401 || error.status === 403)
        ) {
          store.auth.reset()
          throw backToSignIn()
        }
        throw error
      }
    }
  },
})
