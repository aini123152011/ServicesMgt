import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  createUser,
  deleteUser,
  listRoles,
  listUsers,
  updateUser,
  type UserCreatePayload,
  type UserUpdatePayload,
} from '@/api/users'

/** 用户列表，一次全量拉取后由表格本地分页/筛选 */
export function useUsersQuery() {
  return useQuery({ queryKey: ['users'], queryFn: () => listUsers() })
}

/** 角色字典，创建/编辑对话框的角色多选项来源 */
export function useRolesQuery() {
  return useQuery({ queryKey: ['roles'], queryFn: listRoles })
}

export function useCreateUserMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: UserCreatePayload) => createUser(payload),
    onSuccess: () => {
      toast.success('用户已创建')
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
    // 失败经 main.tsx 全局 mutation onError 展示后端 detail，不在此重复处理
  })
}

export function useUpdateUserMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UserUpdatePayload }) =>
      updateUser(id, payload),
    onSuccess: () => {
      toast.success('用户已更新')
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}

export function useDeleteUserMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteUser(id),
    onSuccess: () => {
      toast.success('用户已删除')
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}
