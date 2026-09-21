import { useState } from 'react'
import {
  ArrowLeft,
  Copy,
  Download,
  FileText,
  Folder,
  LoaderCircle,
  RefreshCw,
  Search,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  useServiceDataContentQuery,
  useServiceDataTreeQuery,
} from '../hooks/use-services'

type ServiceDataExplorerProps = {
  name: string
  dataDir: string
}

/** 可选行数档位；文案是「N 行」，按语言拼单位 */
const TAIL_OPTIONS = [100, 200, 500, 1000, 2000]

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

export function ServiceDataExplorer({
  name,
  dataDir,
}: ServiceDataExplorerProps) {
  // 当前浏览的相对子目录，如 "" 或 "2026-09-20"
  const [currentSubpath, setCurrentSubpath] = useState('')
  // 当前选中的文件相对路径，如 "2026-09-20/192.168.1.100.log"
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null)
  // 过滤关键字
  const [keyword, setKeyword] = useState('')
  // 末尾行数
  const [tailLines, setTailLines] = useState(500)
  const { t } = useTranslation()

  const treeQuery = useServiceDataTreeQuery(name, currentSubpath)
  const contentQuery = useServiceDataContentQuery(
    name,
    selectedFilePath ?? '',
    tailLines,
    keyword,
    !!selectedFilePath
  )

  const handleNavigateFolder = (folderName: string) => {
    const nextPath = currentSubpath
      ? `${currentSubpath}/${folderName}`
      : folderName
    setCurrentSubpath(nextPath)
  }

  const handleNavigateUp = () => {
    if (!currentSubpath) return
    const parts = currentSubpath.split('/')
    parts.pop()
    setCurrentSubpath(parts.join('/'))
  }

  const handleSelectFile = (fileName: string) => {
    const fullPath = currentSubpath ? `${currentSubpath}/${fileName}` : fileName
    setSelectedFilePath(fullPath)
  }

  const handleCopyLogs = () => {
    if (!contentQuery.data?.lines) return
    navigator.clipboard.writeText(contentQuery.data.lines.join('\n'))
    toast.success(t('services.data.copySuccess'))
  }

  const handleDownload = () => {
    if (!contentQuery.data || !selectedFilePath) return
    const blob = new Blob([contentQuery.data.lines.join('\n')], {
      type: 'text/plain;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = selectedFilePath.split('/').pop() || 'bmc-log.txt'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  return (
    <Card className='w-full'>
      <CardHeader>
        <div className='flex flex-wrap items-center justify-between gap-4'>
          <div>
            <CardTitle>{t('services.data.title')}</CardTitle>
            <CardDescription>
              {t('services.data.mountPoint', { dir: dataDir })}
              {currentSubpath &&
                ` · ${t('services.data.subpath', { path: currentSubpath })}`}
            </CardDescription>
          </div>
          <div className='flex items-center gap-2'>
            {currentSubpath && (
              <Button
                variant='outline'
                size='sm'
                onClick={handleNavigateUp}
                className='h-8'
              >
                <ArrowLeft className='mr-1 size-3.5' />
                {t('services.data.up')}
              </Button>
            )}
            <Button
              variant='outline'
              size='sm'
              onClick={() => {
                treeQuery.refetch()
                if (selectedFilePath) contentQuery.refetch()
              }}
              className='h-8'
            >
              <RefreshCw
                className={`mr-1 size-3.5 ${treeQuery.isFetching ? 'animate-spin' : ''}`}
              />
              {t('common.refresh')}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className='grid grid-cols-1 gap-6 lg:grid-cols-12'>
          {/* 左侧：文件树列表 */}
          <div className='rounded-md border p-3 lg:col-span-4'>
            <div className='mb-2 flex items-center justify-between text-xs font-semibold text-muted-foreground'>
              <span>
                {t('services.data.entries', {
                  total: treeQuery.data?.entries.length ?? 0,
                })}
              </span>
              <span>{t('services.data.typeAndSize')}</span>
            </div>

            {treeQuery.isPending ? (
              <div className='flex items-center justify-center py-10'>
                <LoaderCircle className='animate-spin text-muted-foreground' />
              </div>
            ) : treeQuery.isError ? (
              <div className='py-6 text-center text-sm text-destructive'>
                {t('services.data.dirLoadFailed')}
              </div>
            ) : treeQuery.data?.entries.length === 0 ? (
              <div className='py-8 text-center text-xs text-muted-foreground'>
                {t('services.data.emptyDir')}
              </div>
            ) : (
              <div className='max-h-96 space-y-1 overflow-y-auto'>
                {treeQuery.data?.entries.map((entry) => {
                  const isDir = entry.type === 'dir'
                  const fullPath = currentSubpath
                    ? `${currentSubpath}/${entry.name}`
                    : entry.name
                  const isSelected = selectedFilePath === fullPath

                  return (
                    <button
                      key={entry.name}
                      type='button'
                      onClick={() =>
                        isDir
                          ? handleNavigateFolder(entry.name)
                          : handleSelectFile(entry.name)
                      }
                      className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted ${
                        isSelected ? 'bg-muted font-medium' : ''
                      }`}
                    >
                      <div className='flex min-w-0 items-center gap-2'>
                        {isDir ? (
                          <Folder className='size-4 shrink-0 text-amber-500' />
                        ) : (
                          <FileText className='size-4 shrink-0 text-sky-500' />
                        )}
                        <span className='truncate'>{entry.name}</span>
                      </div>
                      <span className='shrink-0 text-[11px] text-muted-foreground'>
                        {isDir
                          ? t('services.data.dir')
                          : formatBytes(entry.size)}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {/* 右侧：日志/数据文件内容预览与过滤 */}
          <div className='flex flex-col rounded-md border p-4 lg:col-span-8'>
            {selectedFilePath ? (
              <>
                <div className='mb-3 flex flex-wrap items-center justify-between gap-3 border-b pb-3'>
                  <div className='flex items-center gap-2'>
                    <Badge variant='outline' className='font-mono text-xs'>
                      {selectedFilePath}
                    </Badge>
                    {contentQuery.data && (
                      <span className='text-xs text-muted-foreground'>
                        {t('services.data.fileMeta', {
                          size: formatBytes(contentQuery.data.size),
                          lines: contentQuery.data.lines.length,
                        })}
                        {contentQuery.data.truncated &&
                          t('services.data.truncated')}
                      </span>
                    )}
                  </div>
                  <div className='flex items-center gap-2'>
                    <Button
                      variant='ghost'
                      size='sm'
                      className='h-8 px-2'
                      onClick={handleCopyLogs}
                      title={t('services.data.copyTitle')}
                    >
                      <Copy className='size-3.5' />
                    </Button>
                    <Button
                      variant='ghost'
                      size='sm'
                      className='h-8 px-2'
                      onClick={handleDownload}
                      title={t('services.data.downloadTitle')}
                    >
                      <Download className='size-3.5' />
                    </Button>
                  </div>
                </div>

                {/* 过滤条 */}
                <div className='mb-3 flex flex-wrap items-center gap-2'>
                  <div className='relative min-w-48 flex-1'>
                    <Search className='absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground' />
                    <Input
                      placeholder={t('services.data.filterPlaceholder')}
                      value={keyword}
                      onChange={(e) => setKeyword(e.target.value)}
                      className='h-8 pl-8 text-xs'
                    />
                  </div>
                  <div className='flex items-center gap-1.5'>
                    <span className='text-xs text-muted-foreground'>
                      {t('services.data.lines')}
                    </span>
                    <Select
                      value={String(tailLines)}
                      onValueChange={(v) => setTailLines(Number(v))}
                    >
                      <SelectTrigger className='h-8 w-24 text-xs'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TAIL_OPTIONS.map((option) => (
                          <SelectItem key={option} value={String(option)}>
                            {t('services.data.lineCount', { lines: option })}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                {/* 文本展示区 */}
                {contentQuery.isPending ? (
                  <div className='flex h-72 items-center justify-center'>
                    <LoaderCircle className='animate-spin text-muted-foreground' />
                  </div>
                ) : contentQuery.isError ? (
                  <div className='flex h-72 items-center justify-center text-sm text-destructive'>
                    {t('services.data.readFailed')}
                  </div>
                ) : contentQuery.data?.lines.length === 0 ? (
                  <div className='flex h-72 items-center justify-center text-xs text-muted-foreground'>
                    {keyword
                      ? t('services.data.noMatch')
                      : t('services.data.fileEmpty')}
                  </div>
                ) : (
                  <div className='h-80 overflow-auto rounded-md bg-slate-950 p-3 font-mono text-xs text-slate-100 dark:bg-zinc-950'>
                    {contentQuery.data?.lines.map((line, idx) => (
                      <div key={idx} className='leading-relaxed break-all'>
                        <span className='mr-3 text-slate-500 select-none'>
                          {idx + 1}
                        </span>
                        <span>{line}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className='flex h-80 flex-col items-center justify-center text-muted-foreground'>
                <FileText className='mb-2 size-10 opacity-30' />
                <p className='text-sm'>{t('services.data.selectFile')}</p>
                <p className='mt-1 text-xs text-muted-foreground/70'>
                  {t('services.data.structureHint')}
                </p>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
