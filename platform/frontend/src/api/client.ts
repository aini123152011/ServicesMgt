import axios from 'axios'
import { useAuthStore } from '@/stores/auth-store'

/**
 * 全局 axios 实例：统一指向平台后端（/api/v1 前缀写进各请求路径）。
 * 错误不在此吞掉，原样上抛；401/500 由 main.tsx 的 QueryClient 全局处理。
 */
export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000',
})

// 每次请求实时从 store 读 token，避免闭包捕获过期值
apiClient.interceptors.request.use((config) => {
  const { accessToken } = useAuthStore.getState().auth
  if (accessToken) {
    config.headers.set('Authorization', `Bearer ${accessToken}`)
  }
  return config
})
