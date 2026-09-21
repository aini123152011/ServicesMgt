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
  test: {
    silent: 'passed-only',
    unstubEnvs: true,
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
