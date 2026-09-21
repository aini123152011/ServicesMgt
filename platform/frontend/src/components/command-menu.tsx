import React from 'react'
import { useNavigate } from '@tanstack/react-router'
import { ArrowRight, ChevronRight, Laptop, Moon, Sun } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSearch } from '@/context/search-provider'
import { useTheme } from '@/context/theme-provider'
import { useNavLabel } from '@/hooks/use-nav-label'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command'
import { sidebarData } from './layout/data/sidebar-data'
import { ScrollArea } from './ui/scroll-area'

export function CommandMenu() {
  const navigate = useNavigate()
  const { setTheme } = useTheme()
  const { open, setOpen } = useSearch()
  const { t } = useTranslation()
  // 导航项文案随语言切换（key 或服务名原文）
  const navLabel = useNavLabel()

  const runCommand = React.useCallback(
    (command: () => unknown) => {
      setOpen(false)
      command()
    },
    [setOpen]
  )

  return (
    <CommandDialog
      modal
      open={open}
      onOpenChange={setOpen}
      // 对话框标题/描述是 sr-only 的，模板默认写死英文；ui/command.tsx 是生成物，
      // 按 spec 不改，改由调用方传入本地化文案
      title={t('ui.command.title')}
      description={t('ui.command.description')}
    >
      <CommandInput placeholder={t('ui.command.placeholder')} />
      <CommandList>
        <ScrollArea type='hover' className='h-72 pe-1'>
          <CommandEmpty>{t('ui.command.noResults')}</CommandEmpty>
          {sidebarData.navGroups.map((group) => (
            <CommandGroup
              key={group.titleKey ?? group.title}
              heading={navLabel(group)}
            >
              {group.items.map((navItem, i) => {
                if (navItem.url)
                  return (
                    <CommandItem
                      key={`${navItem.url}-${i}`}
                      value={navLabel(navItem)}
                      onSelect={() => {
                        runCommand(() => navigate({ to: navItem.url }))
                      }}
                    >
                      <div className='flex size-4 items-center justify-center'>
                        <ArrowRight className='size-2 text-muted-foreground/80' />
                      </div>
                      {navLabel(navItem)}
                    </CommandItem>
                  )

                return navItem.items?.map((subItem, i) => (
                  <CommandItem
                    key={`${navLabel(navItem)}-${subItem.url}-${i}`}
                    value={`${navLabel(navItem)}-${subItem.url}`}
                    onSelect={() => {
                      runCommand(() => navigate({ to: subItem.url }))
                    }}
                  >
                    <div className='flex size-4 items-center justify-center'>
                      <ArrowRight className='size-2 text-muted-foreground/80' />
                    </div>
                    {navLabel(navItem)} <ChevronRight /> {navLabel(subItem)}
                  </CommandItem>
                ))
              })}
            </CommandGroup>
          ))}
          <CommandSeparator />
          <CommandGroup heading={t('ui.command.theme')}>
            <CommandItem onSelect={() => runCommand(() => setTheme('light'))}>
              <Sun /> <span>{t('ui.command.light')}</span>
            </CommandItem>
            <CommandItem onSelect={() => runCommand(() => setTheme('dark'))}>
              <Moon className='scale-90' />
              <span>{t('ui.command.dark')}</span>
            </CommandItem>
            <CommandItem onSelect={() => runCommand(() => setTheme('system'))}>
              <Laptop />
              <span>{t('ui.command.system')}</span>
            </CommandItem>
          </CommandGroup>
        </ScrollArea>
      </CommandList>
    </CommandDialog>
  )
}
