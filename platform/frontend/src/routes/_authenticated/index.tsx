import { createFileRoute, redirect } from '@tanstack/react-router'

// 平台没有独立的首页，登录后直接落到服务列表
export const Route = createFileRoute('/_authenticated/')({
  beforeLoad: () => {
    throw redirect({ to: '/services', replace: true })
  },
})
