import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/e2e.spec.ts',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  outputDir: './test-results',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8787',
    channel: process.env.PLAYWRIGHT_CHANNEL,
    // Keep the existing Russian-language assertions independent of browser defaults.
    locale: 'ru-RU',
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
    // Authenticated traces can contain login payloads and session cookies.
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 960 } },
    },
  ],
});
