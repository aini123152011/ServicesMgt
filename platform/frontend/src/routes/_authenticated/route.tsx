import { createFileRoute, redirect } from '@tanstack/react-router'
import { useAuthStore } from '@/stores/auth-store'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'

export const Route = createFileRoute('/_authenticated')({
  component: AuthenticatedLayout,
  // 未登录挡在布局加载之前，并记录来源路径便于登录后回跳
  beforeLoad: ({ location }) => {
    const token = useAuthStore.getState().auth.accessToken
    if (!token) {
      throw redirect({
        to: '/sign-in',
        search: { redirect: `${location.pathname}${location.searchStr}` },
        replace: true,
      })
    }
  },
})
