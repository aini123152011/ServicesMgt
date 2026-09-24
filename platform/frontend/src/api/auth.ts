import { apiClient } from './client'

/** 后端 POST /login/access-token 的返回体 */
interface Token {
  access_token: string
  token_type: string
}

/** 与后端 app/models.py 的 UserPublic 一一对应 */
export interface UserPublic {
  id: string
  email: string
  is_active: boolean
  is_superuser: boolean
  full_name: string | null
  /** 角色名列表（admin/operator/readonly，可多选；is_superuser 等价 admin 但可能不含 'admin'） */
  roles: string[]
  created_at: string | null
  /** 邮箱验证时间；null = 未验证（自助注册的账号必须先用邮件里的验证码完成验证才能登录） */
  email_verified_at: string | null
}

/** 图片验证码：image 是 data URL，可直接塞给 <img src> */
interface Captcha {
  captcha_id: string
  image: string
}

/**
 * 验证码用途。跨用途复用同一张会被服务端拒绝，因此签发与校验必须成对传同一个值。
 */
export type CaptchaScope = 'login' | 'register' | 'resend'

/** 签发一张图片验证码 */
export async function fetchCaptcha(scope: CaptchaScope): Promise<Captcha> {
  const { data } = await apiClient.post<Captcha>('/api/v1/auth/captcha', {
    scope,
  })
  return data
}

/** 注册是否可用：enabled 由管理员开关决定，email_configured 由服务端邮件配置决定 */
interface RegistrationAvailability {
  enabled: boolean
  email_configured: boolean
}

export async function getRegistrationAvailability(): Promise<RegistrationAvailability> {
  const { data } = await apiClient.get<RegistrationAvailability>(
    '/api/v1/auth/registration-available'
  )
  return data
}

interface RegisterPayload {
  email: string
  password: string
  full_name: string | null
  captcha_id: string
  captcha_answer: string
}

/** 提交注册：成功只代表「待验证」，还要用邮件里的 6 位码完成验证才能登录 */
export async function register(
  payload: RegisterPayload
): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    '/api/v1/auth/register',
    payload
  )
  return data
}

/** 用邮件收到的 6 位验证码完成邮箱验证 */
export async function verifyEmail(
  email: string,
  code: string
): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    '/api/v1/auth/verify-email',
    { email, code }
  )
  return data
}

/** 重新发送验证码（服务端 60 秒内只发一次，且响应恒定以防账号枚举） */
export async function resendVerification(
  email: string,
  captchaId: string,
  captchaAnswer: string
): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    '/api/v1/auth/resend-verification',
    { email, captcha_id: captchaId, captcha_answer: captchaAnswer }
  )
  return data
}

/**
 * OAuth2 密码模式登录：按后端约定 username 字段传邮箱。
 *
 * 验证码是**必填**：服务端把它放在凭证校验之前，缺了会直接 400。
 * 失败时后端返回 400/403 { detail }，错误原样上抛由调用方展示。
 */
export async function loginAccessToken(
  email: string,
  password: string,
  captcha: { captchaId: string; captchaAnswer: string }
): Promise<Token> {
  const form = new URLSearchParams({
    username: email,
    password,
    captcha_id: captcha.captchaId,
    captcha_answer: captcha.captchaAnswer,
  })
  const { data } = await apiClient.post<Token>(
    '/api/v1/login/access-token',
    form
  )
  return data
}

/** 获取当前登录用户，依赖请求拦截器已附带 Bearer token */
export async function getCurrentUser(): Promise<UserPublic> {
  const { data } = await apiClient.get<UserPublic>('/api/v1/users/me')
  return data
}
