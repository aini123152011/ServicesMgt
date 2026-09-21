import en from '@/locales/en.json'
import zh from '@/locales/zh.json'
import i18n, { type TFunction } from 'i18next'
import { initReactI18next } from 'react-i18next'

/**
 * i18n 初始化与语言偏好读写。
 *
 * 不引 i18next-browser-languagedetector：本平台的语言来源只有「用户显式选择」，
 * 用 localStorage 一个键就够，避免浏览器语言悄悄改变界面语言。
 * 资源内联打包（不用 http backend）并同步初始化（initImmediate: false），
 * 保证首屏直接是目标语言——不会先渲染一遍 key 再替换成译文。
 */

/** localStorage 键名，与 design.md §3.1 的约定一致 */
export const LANGUAGE_STORAGE_KEY = 'platform.lang'

export const SUPPORTED_LANGUAGES = ['zh', 'en'] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]

/** 默认语言固定中文：没选过语言的用户首屏必须是中文 */
export const DEFAULT_LANGUAGE: Language = 'zh'

export function isLanguage(value: unknown): value is Language {
  return SUPPORTED_LANGUAGES.some((language) => language === value)
}

/**
 * 读取用户选择的语言；无值或脏值（手改 localStorage、旧版本残留）一律回落中文，
 * 避免 i18next 落到一个没有资源的语言上而外露 key。
 */
export function getStoredLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
    return isLanguage(stored) ? stored : DEFAULT_LANGUAGE
  } catch {
    // localStorage 不可用（隐私模式等）：按默认语言渲染，不阻断首屏
    return DEFAULT_LANGUAGE
  }
}

/** 同步 <html lang>：屏幕阅读器、浏览器断行与翻译插件都依赖它 */
function syncDocumentLanguage(language: Language) {
  if (typeof document !== 'undefined') {
    document.documentElement.lang = language
  }
}

/** 切换语言：写 localStorage + 同步 <html lang> + 切换 i18next，界面立即重渲染 */
export async function setLanguage(language: Language) {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  } catch {
    // 持久化失败不影响本次切换（刷新后会回到默认语言）
  }
  syncDocumentLanguage(language)
  await i18n.changeLanguage(language)
}

void i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: getStoredLanguage(),
  fallbackLng: DEFAULT_LANGUAGE,
  // 内联资源同步初始化，避免首屏闪一下 key 或 fallback 语言
  // （i18next v26 的选项名是 initAsync，旧版为 initImmediate）
  initAsync: false,
  // React 自己会转义，i18next 再转义会把 & < > 显示成实体
  interpolation: { escapeValue: false },
  // 资源同步就绪，不需要 Suspense 边界（否则首屏会挂起）
  react: { useSuspense: false },
})

syncDocumentLanguage(getStoredLanguage())

/** 嵌套资源拍平成 'ns.key' 集合，供 key 判定与中英一致性测试复用 */
export function flattenKeys(
  source: Record<string, unknown>,
  prefix = ''
): string[] {
  return Object.entries(source).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return isRecord(value) ? flattenKeys(value, path) : [path]
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/**
 * 资源里的合法 key 联合类型（由中文资源推导）。
 * 模块级数据（导航配置、zod 校验消息）只存 key、不调 t，用它在编译期挡住拼错的 key。
 */
export type TranslationKey = LeafPaths<typeof zh>

type LeafPaths<T> = T extends object
  ? {
      [K in keyof T & string]: T[K] extends object
        ? `${K}.${LeafPaths<T[K]>}`
        : K
    }[keyof T & string]
  : never

/** 中文资源的 key 集合，用于「消息是不是 key」的运行时判定 */
const translationKeys = new Set(flattenKeys(zh))

/** 带参消息的分隔符：`key|name=value|name2=value2`，参数在文案里按名字插值 */
const MESSAGE_PARAM_SEPARATOR = '|'

/**
 * 运行时插值入口。
 *
 * i18next 开了资源类型约束后，t() 的 options 类型是按具体 key 逐字面量推导的，
 * 承载不了「key 与参数都在运行时才知道」的调用，故把这一处边界收敛成一个简单签名。
 * 影响面仅限此函数：静态 t('...') 调用仍完整受编译期 key 检查保护。
 */
const interpolate: (key: string, options?: Record<string, string>) => string =
  i18n.t as unknown as (key: string, options?: Record<string, string>) => string

/**
 * 翻译一条可能是 key 的消息。
 *
 * zod schema 在模块级创建（那时取不到最新语言），所以校验消息统一存 key，
 * 到渲染处再翻译——语言切换后已显示的错误提示也会立即跟着变。
 * 需要带上行号/上下限这类值时写成 `key|name=value`，避免在 schema 里拼出中文句子。
 * 不是 key 的消息（后端 detail、浏览器原生错误）原样返回，
 * 于是「本地化文案」与「后端原文」可以共用同一条展示链路。
 */
export function translateMessage(t: TFunction, message: string): string {
  const [key, ...rawParams] = message.split(MESSAGE_PARAM_SEPARATOR)
  if (!key || !translationKeys.has(key)) return message

  const params: Record<string, string> = {}
  for (const item of rawParams) {
    const separator = item.indexOf('=')
    if (separator > 0) {
      params[item.slice(0, separator)] = item.slice(separator + 1)
    }
  }

  if (rawParams.length === 0) {
    // key 已由运行时集合确认存在，收窄后交给带类型约束的 t
    return t(key as TranslationKey)
  }
  return interpolate(key, params)
}

export default i18n
