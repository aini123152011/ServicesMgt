import { Separator } from '@/components/ui/separator'

type ContentSectionProps = {
  title: string
  description: string
  children: React.ReactNode
}

/**
 * 设置页的分节标题 + 描述。
 *
 * 相比模板版本去掉了内部的定高滚动容器：外层 Main 已经负责整页滚动，
 * 再套一层 overflow 会产生两个滚动条，并把长表单截断在固定高度里。
 */
export function ContentSection({
  title,
  description,
  children,
}: ContentSectionProps) {
  return (
    <div className='flex flex-1 flex-col'>
      <div className='flex-none'>
        <h3 className='text-lg font-medium'>{title}</h3>
        <p className='text-sm text-muted-foreground'>{description}</p>
      </div>
      <Separator className='my-4 flex-none' />
      <div className='flex flex-col gap-4'>{children}</div>
    </div>
  )
}
