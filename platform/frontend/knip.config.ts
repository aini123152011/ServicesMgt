import type { KnipConfig } from 'knip'

const config: KnipConfig = {
  ignore: [
    'src/components/ui/**',
    // 全局类型增强文件：没有 import 语句，但 tsconfig 会加载它（i18next key 类型约束）
    'src/i18n.d.ts',
    'src/tanstack-table.d.ts',
  ],
}

export default config