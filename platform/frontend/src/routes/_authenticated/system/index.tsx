import { createFileRoute, redirect } from '@tanstack/react-router'

// 系统信息与更新已迁入设置页；保留旧路径做重定向，避免既有书签失效
export const Route = createFileRoute('/_authenticated/system/')({
  beforeLoad: () => {
    throw redirect({ to: '/settings/updates', replace: true })
  },
})
