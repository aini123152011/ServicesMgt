import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area'
import { useServiceLogsQuery } from '../hooks/use-services'

type ServiceLogsDialogProps = {
  name: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ServiceLogsDialog({
  name,
  open,
  onOpenChange,
}: ServiceLogsDialogProps) {
  // enabled 随开关：关闭时不请求，打开才拉取最近日志
  const logsQuery = useServiceLogsQuery(name, open)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-3xl'>
        <DialogHeader>
          <DialogTitle>容器日志</DialogTitle>
          <DialogDescription>
            {name} 最近 200 行日志，打开时拉取一次。
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className='h-96 rounded-md border bg-muted/50'>
          <pre className='p-4 font-mono text-xs break-all whitespace-pre-wrap'>
            {logsQuery.isPending
              ? '加载中…'
              : (logsQuery.data?.logs ?? '').trim() || '（暂无日志）'}
          </pre>
          <ScrollBar orientation='horizontal' />
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
