import { useMemo } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { AlertTriangle, LoaderCircle } from 'lucide-react'
import { type ServiceConfig, type ServiceField } from '@/api/services'
import { usePermissions } from '@/hooks/use-permissions'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useUpdateConfigMutation } from '../hooks/use-services'

type ServiceConfigFormProps = {
  name: string
  fields: ServiceField[]
  config: ServiceConfig
}

// 空串/纯空白视为未填写，交给 required 或 optional 逻辑处理
function emptyToUndefined(v: unknown) {
  return typeof v === 'string' && v.trim() === '' ? undefined : v
}

// 后端 pattern 编译失败时返回 null 并跳过该校验，避免脏 schema 令表单崩溃
function compilePattern(pattern?: string): RegExp | null {
  if (!pattern) return null
  try {
    return new RegExp(pattern)
  } catch {
    return null
  }
}

// Textarea 每行一项：拆分 + 去首尾空白 + 丢弃空行
function splitList(raw: string): string[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

/**
 * 据单个 ServiceField 构造 zod 校验器：提交时 integer 已转数字、list 已转数组。
 * 输出类型统一收敛为 ZodType，便于动态拼进 z.object。
 */
function buildFieldSchema(field: ServiceField): z.ZodType {
  switch (field.type) {
    case 'boolean':
      return z.boolean({ error: '必须为布尔值' })
    case 'integer': {
      const num = z.coerce
        .number({
          // 空值在 preprocess 已转为 undefined，借此区分"必填缺失"与"不是数字"
          error: (iss) =>
            iss.input === undefined ? '此项为必填' : '必须为整数',
        })
        .int('必须为整数')
      const withMin =
        field.min !== undefined
          ? num.min(field.min, `不能小于 ${field.min}`)
          : num
      const withMax =
        field.max !== undefined
          ? withMin.max(field.max, `不能大于 ${field.max}`)
          : withMin
      // 非必填允许留空，输出 undefined 后 JSON 序列化会自动丢弃该键
      return z.preprocess(
        emptyToUndefined,
        field.required ? withMax : withMax.optional()
      )
    }
    case 'enum': {
      const options = field.options ?? []
      return z.string().superRefine((v, ctx) => {
        if (v === '') {
          if (field.required) {
            ctx.addIssue({ code: 'custom', message: '此项为必填' })
          }
          return
        }
        if (!options.includes(v)) {
          ctx.addIssue({ code: 'custom', message: '请选择有效选项' })
        }
      })
    }
    case 'list': {
      const itemRe = compilePattern(field.item_pattern)
      return z
        .string()
        .superRefine((v, ctx) => {
          const items = splitList(v)
          if (items.length === 0) {
            if (field.required) {
              ctx.addIssue({ code: 'custom', message: '至少填写一项' })
            }
            return
          }
          // 逐项校验并带行号报错，方便用户定位
          if (itemRe) {
            items.forEach((item, idx) => {
              if (!itemRe.test(item)) {
                ctx.addIssue({
                  code: 'custom',
                  message: `第 ${idx + 1} 行「${item}」不符合格式要求`,
                })
              }
            })
          }
        })
        .transform(splitList)
    }
    case 'text': {
      return z.string().superRefine((v, ctx) => {
        if (field.required && v.trim() === '') {
          ctx.addIssue({ code: 'custom', message: '此项为必填' })
          return
        }
        if (field.pem && v.trim()) {
          const stripped = v.trim()
          if (
            !stripped.startsWith('-----BEGIN ') ||
            !stripped.includes('-----END ')
          ) {
            ctx.addIssue({
              code: 'custom',
              message: '必须为有效的 PEM 格式证书或密钥',
            })
          }
        }
      })
    }
    case 'string':
    default: {
      const re = compilePattern(field.pattern)
      return z.string().superRefine((v, ctx) => {
        if (field.required && v.trim() === '') {
          ctx.addIssue({ code: 'custom', message: '此项为必填' })
          return
        }
        if (re && v !== '' && !re.test(v)) {
          ctx.addIssue({ code: 'custom', message: '内容不符合格式要求' })
        }
      })
    }
  }
}

// 后端值/默认值 → 表单控件值：list 转多行文本，其余转字符串或布尔
function toFormValue(field: ServiceField, raw: unknown): unknown {
  if (field.type === 'list') {
    if (Array.isArray(raw)) return raw.map(String).join('\n')
    return typeof raw === 'string' ? raw : ''
  }
  if (field.type === 'boolean') return raw === true
  return raw === undefined || raw === null ? '' : String(raw)
}

export function ServiceConfigForm({
  name,
  fields,
  config,
}: ServiceConfigFormProps) {
  const updateMutation = useUpdateConfigMutation(name)
  // readonly 角色只看不改：字段与提交按钮一并禁用
  const { isOperator } = usePermissions()
  const canEdit = isOperator

  // 分离基础配置与故障注入字段
  const baseFields = useMemo(
    () => fields.filter((f) => (f.group ?? 'base') === 'base'),
    [fields]
  )
  const faultFields = useMemo(
    () => fields.filter((f) => f.group === 'fault'),
    [fields]
  )

  // schema/fields 来自 query 缓存，identity 稳定，useMemo 避免每渲染重建校验器
  const formSchema = useMemo(
    () =>
      z.object(
        Object.fromEntries(
          fields.map((f): [string, z.ZodType] => [f.name, buildFieldSchema(f)])
        )
      ),
    [fields]
  )
  type FormInput = z.input<typeof formSchema>
  type FormOutput = z.output<typeof formSchema>

  // 已保存值优先于 schema 默认值；保存成功后不 reset，保留用户当前编辑内容
  const defaultValues = useMemo(
    () =>
      Object.fromEntries(
        fields.map((f) => [
          f.name,
          toFormValue(f, config.values?.[f.name] ?? f.default),
        ])
      ),
    [fields, config.values]
  )

  const form = useForm<FormInput, unknown, FormOutput>({
    resolver: zodResolver(formSchema),
    defaultValues,
  })

  const onSubmit = (values: FormOutput) => {
    updateMutation.mutate(values)
  }

  const renderField = (f: ServiceField) => (
    <FormField
      key={f.name}
      control={form.control}
      name={f.name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>
            {f.label}
            {f.required && <span className='text-destructive'> *</span>}
            {f.secret && (
              <span className='ml-1 text-xs text-muted-foreground'>
                (敏感脱敏字段)
              </span>
            )}
          </FormLabel>
          {f.type === 'boolean' ? (
            <FormControl>
              <Switch
                checked={field.value === true}
                onCheckedChange={field.onChange}
                disabled={!canEdit}
              />
            </FormControl>
          ) : f.type === 'enum' ? (
            <FormControl>
              <Select
                value={String(field.value ?? '')}
                onValueChange={field.onChange}
                disabled={!canEdit}
              >
                <SelectTrigger className='w-72'>
                  <SelectValue placeholder='请选择' />
                </SelectTrigger>
                <SelectContent>
                  {f.options?.map((opt) => (
                    <SelectItem key={opt} value={opt}>
                      {opt}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormControl>
          ) : f.type === 'list' ? (
            <FormControl>
              <Textarea
                {...field}
                value={String(field.value ?? '')}
                rows={Math.min(
                  Math.max(splitList(String(field.value ?? '')).length + 1, 3),
                  10
                )}
                className='font-mono'
                disabled={!canEdit}
                placeholder={'每行一项'}
              />
            </FormControl>
          ) : f.type === 'text' ? (
            <FormControl>
              <Textarea
                {...field}
                value={String(field.value ?? '')}
                rows={f.pem ? 8 : 4}
                className='font-mono text-xs'
                disabled={!canEdit}
                placeholder={
                  f.pem
                    ? '-----BEGIN CERTIFICATE / PRIVATE KEY-----\n...\n-----END CERTIFICATE / PRIVATE KEY-----'
                    : ''
                }
              />
            </FormControl>
          ) : f.secret ? (
            <FormControl>
              <PasswordInput
                {...field}
                value={String(field.value ?? '')}
                placeholder={
                  field.value === '********'
                    ? '保持既有密钥不变（输入新值覆盖）'
                    : '请输入密码或密钥'
                }
                className='w-72'
                disabled={!canEdit}
              />
            </FormControl>
          ) : (
            <FormControl>
              <Input
                {...field}
                value={String(field.value ?? '')}
                type={f.type === 'integer' ? 'number' : 'text'}
                min={f.min}
                max={f.max}
                className='w-72'
                disabled={!canEdit}
              />
            </FormControl>
          )}
          {f.help && <FormDescription>{f.help}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  )

  return (
    <Form {...form}>
      <form
        id='service-config-form'
        onSubmit={form.handleSubmit(onSubmit)}
        className='space-y-6'
      >
        {!canEdit && (
          <p className='text-sm text-muted-foreground'>
            只读角色无权修改配置。
          </p>
        )}
        {config.applied === false && (
          <p className='text-sm text-amber-600 dark:text-amber-400'>
            当前保存的配置尚未生效（容器未运行），启动服务后加载。
          </p>
        )}
        {config.rendered_at && (
          <p className='text-sm text-muted-foreground'>
            最近渲染时间：{config.rendered_at}
          </p>
        )}

        {faultFields.length > 0 ? (
          <Tabs defaultValue='base' className='w-full'>
            <TabsList className='mb-4'>
              <TabsTrigger value='base'>
                基础服务配置 ({baseFields.length})
              </TabsTrigger>
              <TabsTrigger
                value='fault'
                className='text-amber-600 dark:text-amber-400'
              >
                BMC 故障注入 ({faultFields.length})
              </TabsTrigger>
            </TabsList>
            <TabsContent value='base' className='space-y-6'>
              {baseFields.map(renderField)}
            </TabsContent>
            <TabsContent value='fault' className='space-y-6'>
              <div className='flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-900 dark:text-amber-200'>
                <AlertTriangle className='mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400' />
                <div>
                  <strong>BMC 故障注入模式：</strong>
                  本分组配置项专用于 BMC
                  带外管理服务器的健壮性、超时重试与异常处理逻辑测试。在正常测试模式下请保持为
                  &quot;none&quot;。
                </div>
              </div>
              {faultFields.map(renderField)}
            </TabsContent>
          </Tabs>
        ) : (
          <div className='space-y-6'>{baseFields.map(renderField)}</div>
        )}

        <div className='flex gap-2 pt-2'>
          <Button
            type='submit'
            disabled={!canEdit || updateMutation.isPending}
            title={canEdit ? undefined : '只读角色无权修改配置'}
          >
            {updateMutation.isPending && (
              <LoaderCircle className='animate-spin' />
            )}
            保存配置
          </Button>
          <Button
            type='button'
            variant='outline'
            onClick={() => form.reset(defaultValues)}
            disabled={!canEdit}
          >
            还原
          </Button>
        </div>
      </form>
    </Form>
  )
}
