import { createFileRoute, redirect } from '@tanstack/react-router'

// 设置页默认落在「账号」：/settings 本身没有内容，直接改地址而不渲染空页
export const Route = createFileRoute('/_authenticated/settings/')({
  beforeLoad: () => {
    throw redirect({ to: '/settings/account', replace: true })
  },
})
