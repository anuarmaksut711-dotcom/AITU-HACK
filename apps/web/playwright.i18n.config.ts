import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests', testMatch: 'i18n.spec.ts', fullyParallel: true, workers: 2,
  timeout: 30_000, reporter: 'list', outputDir: './test-results/i18n',
  use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:5197', locale: 'ru-RU', channel: process.env.PLAYWRIGHT_CHANNEL, trace: 'off' },
  webServer: { command: 'npm run dev -- --port 5197 --strictPort', url: 'http://127.0.0.1:5197', reuseExistingServer: !process.env.CI },
})
