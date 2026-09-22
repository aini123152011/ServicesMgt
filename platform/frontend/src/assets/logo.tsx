import { type SVGProps } from 'react'
import { cn } from '@/lib/utils'

/**
 * 平台标记：机架外壳里的一颗被管理芯片，对应「BMC 测试仪器」定位。
 *
 * 描边风格与 lucide 图标一致（随 currentColor 取色，不引入品牌色变量），
 * 24×24 的几何在 16px 下仍能读出芯片轮廓。
 */
export function Logo({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox='0 0 24 24'
      xmlns='http://www.w3.org/2000/svg'
      height='24'
      width='24'
      fill='none'
      stroke='currentColor'
      strokeWidth='2'
      strokeLinecap='round'
      strokeLinejoin='round'
      className={cn('size-6', className)}
      {...props}
    >
      <title>BMC Services Platform</title>
      {/* 机架外壳 */}
      <rect x='3' y='4' width='18' height='16' rx='3' />
      {/* 被管理的 BMC 芯片 */}
      <rect x='9' y='9' width='6' height='6' rx='1.5' />
      {/* 芯片四边各一根引线接到外壳 */}
      <path d='M12 6v3M12 15v3M6 12h3M15 12h3' />
    </svg>
  )
}
