/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 平台后端基址（不含 /api/v1 前缀） */
  readonly VITE_API_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
