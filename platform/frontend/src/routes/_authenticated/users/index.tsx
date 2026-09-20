import z from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { ROLE_NAMES } from '@/api/users'
import { Users } from '@/features/users'

const usersSearchSchema = z.object({
  page: z.number().optional().catch(1),
  pageSize: z.number().optional().catch(10),
  // Facet filters
  status: z
    .array(z.enum(['active', 'inactive']))
    .optional()
    .catch([]),
  role: z.array(z.enum(ROLE_NAMES)).optional().catch([]),
  // Per-column text filter (email)
  email: z.string().optional().catch(''),
})

export const Route = createFileRoute('/_authenticated/users/')({
  validateSearch: usersSearchSchema,
  component: Users,
})
