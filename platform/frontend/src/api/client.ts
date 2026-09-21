import axios from 'axios'
import { useAuthStore } from '@/stores/auth-store'

/**
 * 全局 axios 实例：统一指向平台后端（/api/v1 前缀写进各请求路径）。
 * 错误不在此吞掉，原样上抛；401/500 由 main.tsx 的 QueryClient 全局处理。
 *
 * 基地址缺省为空串（相对路径 = 当前源）：线上是单容器同源部署，前端产物与
 * /api/v1 由同一后端提供。NOTE: 兜底值不能写绝对地址——未设置 VITE_API_URL 时
 * 会把线上构建指向「访问者自己的 localhost」，页面能打开但所有请求都失败。
 * 本地开发（前端 :5173 + 后端 :8000）在 .env.local 里显式设置 VITE_API_URL。
 */
export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? '',
})

// 每次请求实时从 store 读 token，避免闭包捕获过期值
apiClient.interceptors.request.use((config) => {
  const { accessToken } = useAuthStore.getState().auth
  if (accessToken) {
    config.headers.set('Authorization', `Bearer ${accessToken}`)
  }
  return config
})
