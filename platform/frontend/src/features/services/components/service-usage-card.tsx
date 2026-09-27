import { Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { type ServicePort, type ServiceUsageEntry } from '@/api/services'
import { copyText } from '@/lib/clipboard'
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
  /** manifest.ports：{{port}} 取第一个端口，{{port:443}} 取容器端口为 443 的那一项 */
  ports?: ServicePort[] | null
  /** 该服务在二层测试网段上的地址；给了就用它替换 {{host}}（见 fillPlaceholders） */
  l2Address?: string | null
}

/**
 * 替换命令里的 {{host}}/{{port}}：manifest 里不写死地址与端口，换台机器示例依然可用。
 *
 * {{host}} 默认取访问平台用的地址（`window.location.hostname`），但**绑定了二层测试网段的服务
 * 要用它在测试网段上的地址**（`l2Address`）——被测 BMC 在测试网段上够不到管理网地址，
 * 照抄管理网地址会直接连不上。
 *
 * {{port}} 必须跟着 {{host}} 一起变，因为**容器端口只在容器网络里可用**：按管理网地址
 * 访问时要用宿主机发布端口（nginx 容器 80 → 宿主 18102），按二层地址访问时反而只能用
 * 容器端口（发布端口在测试网段上不通）。{{port:443}} 是同一套规则、按容器端口号取
 * ——多端口服务（nginx 的 443、RADIUS 的 1813）才指得准。
 *
 * 端口未知（manifest 没声明该端口，或服务未部署且无发布端口）时保留占位符原文：
 * 宁可让用户看见没替换的 {{port}}，也不给一个连不上的端口。顺带去首尾空白——
 * YAML 块标量（`command: |`）自带一个结尾换行。
 */
function fillPlaceholders(
  command: string,
  ports?: ServicePort[] | null,
  l2Address?: string | null
): string {
  const l2 = l2Address?.trim()
  const host = l2 || window.location.hostname
  return command
    .trim()
    .replace(/\{\{host\}\}/g, host)
    .replace(/\{\{port(?::(\d+))?\}\}/g, (placeholder, wanted?: string) => {
      const containerPort =
        wanted === undefined ? ports?.[0]?.port : Number(wanted)
      const entry = ports?.find((item) => item.port === containerPort)
      if (!entry) return placeholder
      // 二层地址下用容器端口：宿主发布端口在测试网段上不通
      return String(l2 ? entry.port : (entry.host_port ?? entry.port))
    })
}

export function ServiceUsageCard({
  entries,
  ports,
  l2Address,
}: ServiceUsageCardProps) {
  const { t } = useTranslation()

  // 没有 usage 的服务不渲染卡片，避免留一张空卡片占位置
  if (!entries || entries.length === 0) return null

  const handleCopy = async (command: string) => {
    // 复制成功才说成功：http 访问下 Clipboard API 不存在，靠 copyText 里的降级路径兜底
    if (await copyText(command)) {
      toast.success(t('services.usage.copySuccess'))
    } else {
      toast.error(t('common.copyFailed'))
    }
  }

  return (
    <Card className='w-full'>
      <CardHeader>
        <CardTitle>{t('services.usage.title')}</CardTitle>
        <CardDescription>{t('services.usage.description')}</CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-3'>
        {entries.map((entry, index) => {
          const command = fillPlaceholders(entry.command, ports, l2Address)
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
