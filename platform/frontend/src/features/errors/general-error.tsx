import { useNavigate, useRouter } from '@tanstack/react-router'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

type GeneralErrorProps = React.HTMLAttributes<HTMLDivElement> & {
  minimal?: boolean
}

export function GeneralError({
  className,
  minimal = false,
}: GeneralErrorProps) {
  const navigate = useNavigate()
  const { history } = useRouter()
  return (
    <div className={cn('h-svh w-full', className)}>
      <div className='m-auto flex h-full w-full flex-col items-center justify-center gap-2'>
        {!minimal && (
          <h1 className='text-[7rem] leading-tight font-bold'>500</h1>
        )}
        <span className='font-medium'>服务器内部错误</span>
        <p className='text-center text-muted-foreground'>
          服务器处理请求时出错，请稍后重试；若持续出现请联系管理员。
        </p>
        {!minimal && (
          <div className='mt-6 flex gap-4'>
            <Button variant='outline' onClick={() => history.go(-1)}>
              返回上一页
            </Button>
            <Button onClick={() => navigate({ to: '/' })}>回到首页</Button>
          </div>
        )}
      </div>
    </div>
  )
}
