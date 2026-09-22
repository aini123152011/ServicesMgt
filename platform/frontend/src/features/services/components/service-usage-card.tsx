import { Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { type ServiceUsageEntry } from '@/api/services'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

type ServiceUsageCardProps = {
  /** manifest.usage；未声明或为空时不渲染卡片 */
  entries?: ServiceUsageEntry[] | null
  /** 该服务 manifest 的第一个端口，用于替换命令里的 {{port}} */
  port?: number
}

/**
 * 替换命令里的 {{host}}/{{port}}：manifest 里不写死地址，换台机器示例依然可用。
 * 端口缺失时保留占位符原文——宁可让用户看见没替换的 {{port}}，也不给一个错误端口。
 * 顺带去掉首尾空白：YAML 块标量（`command: |`）自带一个结尾换行。
 */
function fillPlaceholders(command: string, port?: number): string {
  return command
    .trim()
    .replace(/\{\{host\}\}/g, window.location.hostname)
    .replace(/\{\{port\}\}/g, port === undefined ? '{{port}}' : String(port))
}

export function ServiceUsageCard({ entries, port }: ServiceUsageCardProps) {
  const { t } = useTranslation()

  // 没有 usage 的服务不渲染卡片，避免留一张空卡片占位置
  if (!entries || entries.length === 0) return null

  const handleCopy = (command: string) => {
    navigator.clipboard.writeText(command)
    toast.success(t('services.usage.copySuccess'))
  }

  return (
    <Card className='w-full'>
      <CardHeader>
        <CardTitle>{t('services.usage.title')}</CardTitle>
        <CardDescription>{t('services.usage.description')}</CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-3'>
        {entries.map((entry, index) => {
          const command = fillPlaceholders(entry.command, port)
          return (
            <div key={index} className='rounded-md border p-3'>
              <div className='flex flex-wrap items-center gap-2'>
                <Badge variant='outline'>{entry.target}</Badge>
                <span className='text-sm text-muted-foreground'>
                  {entry.summary}
                </span>
              </div>
              <div className='mt-2 flex items-start gap-2'>
                {/* 等宽 + 横向滚动 + 不折行：命令折行后看不出断在哪里，照抄时容易漏字符 */}
                <pre className='min-w-0 flex-1 overflow-x-auto rounded-md bg-slate-950 p-3 font-mono text-xs whitespace-pre text-slate-100 dark:bg-zinc-950'>
                  {command}
                </pre>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => handleCopy(command)}
                >
                  <Copy className='size-3.5' />
                  {t('services.usage.copy')}
                </Button>
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
