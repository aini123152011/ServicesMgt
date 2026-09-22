/// <reference types="vitest/config" />
import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import { playwright } from '@vitest/browser-playwright'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  optimizeDeps: {
    // 组件测试首次用到某个 Radix 包时，Vite 会现做依赖预构建并重载测试页，
    // 正在跑的文件会因页面刷新而中断（实测 sign-out-dialog 因此变成 0 test）。
    // 这里显式声明，避免测试期出现「依赖优化触发重载」的偶发失败。
    include: ['@radix-ui/react-popover'],
  },
  test: {
    silent: 'passed-only',
    unstubEnvs: true,
    // 组件测试直接渲染，不走 main.tsx 那条初始化链；先加载 i18n 才拿得到译文而不是 key。
    // 默认语言是中文，与线上未选择语言时的首屏一致。
    setupFiles: ['./src/lib/i18n.ts'],
    browser: {
      enabled: true,
      // 组件测试跑在真实浏览器里：Radix 的 portal/焦点管理与真实 CSS 在 jsdom 下不可靠。
      // Playwright 在这里只是「浏览器驱动」（Vitest 浏览器模式），不是本项目的 E2E 工具
      // ——端到端验收是 scripts/verify_bmc_platform_e2e.py 的真实协议套件。
      // 默认用 Playwright 自带的 chromium（需 `pnpm test:browser:install` 下载一次）；
      // 机器上没有该二进制时可用系统已装的 Chrome：
      //   VITEST_BROWSER_CHANNEL=chrome pnpm test
      provider: playwright(
        process.env.VITEST_BROWSER_CHANNEL
          ? { launchOptions: { channel: process.env.VITEST_BROWSER_CHANNEL } }
          : undefined
      ),
      instances: [{ browser: 'chromium' }],
    },
    coverage: {
      // include: ['src/**/*.{js,jsx,ts,tsx}'], // Uncomment to expand the report to all src/**/* so untested modules appear as 0% coverage.
      exclude: [
        'src/components/ui/**',
        'src/assets/**',
        'src/tanstack-table.d.ts',
        'src/routeTree.gen.ts',
        'src/test-utils/**',
        'src/routes/**',
      ],
    },
  },
})
