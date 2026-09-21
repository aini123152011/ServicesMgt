import { createFileRoute } from '@tanstack/react-router'
import { System } from '@/features/system'

export const Route = createFileRoute('/_authenticated/system/')({
  component: System,
})
