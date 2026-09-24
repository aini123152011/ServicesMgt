import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { Otp } from '@/features/auth/otp'

// email 由注册页跳转时带上；直接访问时缺省为空，页面会把人引回注册页
const searchSchema = z.object({
  email: z.string().optional(),
})

export const Route = createFileRoute('/(auth)/otp')({
  component: Otp,
  validateSearch: searchSchema,
})
