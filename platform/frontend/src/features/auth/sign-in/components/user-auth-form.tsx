import { useState } from 'react'
import { z } from 'zod'
import { AxiosError } from 'axios'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useNavigate } from '@tanstack/react-router'
import { Loader2, LogIn } from 'lucide-react'
import { toast } from 'sonner'
import { getCurrentUser, loginAccessToken } from '@/api/auth'
import { useAuthStore } from '@/stores/auth-store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'

const formSchema = z.object({
  email: z.email({
    error: (iss) => (iss.input === '' ? 'Please enter your email.' : undefined),
  }),
  password: z
    .string()
    .min(1, 'Please enter your password.')
    .min(7, 'Password must be at least 7 characters long.'),
})

interface UserAuthFormProps extends React.HTMLAttributes<HTMLFormElement> {
  redirectTo?: string
}

/** 后端失败响应统一带 { detail }，优先展示给用户 */
function resolveErrorMessage(error: unknown): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail.length > 0) {
      return detail
    }
  }
  return 'Sign in failed. Please try again.'
}

export function UserAuthForm({
  className,
  redirectTo,
  ...props
}: UserAuthFormProps) {
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()
  const { auth } = useAuthStore()

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      email: '',
      password: '',
    },
  })

  function onSubmit(data: z.infer<typeof formSchema>) {
    setIsLoading(true)

    const signIn = async () => {
      try {
        const token = await loginAccessToken(data.email, data.password)
        // 先落 token，请求 /users/me 时拦截器才能附带 Authorization
        auth.setAccessToken(token.access_token)
        const user = await getCurrentUser()
        auth.setUser(user)

        // 回跳地址可能是带查询串的路径，解析后交给 navigate 保证路由匹配
        const target = redirectTo
          ? new URL(redirectTo, window.location.origin)
          : null
        const search = target
          ? Object.fromEntries(target.searchParams)
          : undefined
        navigate({
          to: target?.pathname ?? '/',
          search: search && Object.keys(search).length > 0 ? search : undefined,
          replace: true,
        })

        return `Welcome back, ${user.email}!`
      } catch (error) {
        // 任一步失败都清掉半套凭证，避免"有 token 无 user"的中间态
        auth.reset()
        throw error
      }
    }

    toast.promise(signIn(), {
      loading: 'Signing in...',
      success: (message) => {
        setIsLoading(false)
        return message
      },
      error: (error) => {
        setIsLoading(false)
        return resolveErrorMessage(error)
      },
    })
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        className={cn('grid gap-3', className)}
        {...props}
      >
        <FormField
          control={form.control}
          name='email'
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input placeholder='name@example.com' {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name='password'
          render={({ field }) => (
            <FormItem className='relative'>
              <FormLabel>Password</FormLabel>
              <FormControl>
                <PasswordInput placeholder='********' {...field} />
              </FormControl>
              <FormMessage />
              <Link
                to='/forgot-password'
                className='absolute inset-e-0 -top-0.5 text-sm font-medium text-muted-foreground hover:opacity-75'
              >
                Forgot password?
              </Link>
            </FormItem>
          )}
        />
        <Button className='mt-2' disabled={isLoading}>
          {isLoading ? <Loader2 className='animate-spin' /> : <LogIn />}
          Sign in
        </Button>
      </form>
    </Form>
  )
}
