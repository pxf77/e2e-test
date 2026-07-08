import { defineConfig } from 'playwright/test';
import path from 'path';

const reportDir = process.env.REPORT_DIR ?? 'tc-exec';

export default defineConfig({
  testDir: './products',
  testIgnore: [
    '**/.pytest_tmp/**',
    '**/.pytest_tmp_codex_case_merge/**',
    '**/tmp_pytest_case_merge/**',
    '**/xiaoqinglong-previous/**',
  ],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [
    ['list'],
    ['html', {
      outputFolder: path.join(reportDir, 'reports'),
      open: 'never',
    }],
    ...(process.env.PLAYWRIGHT_JSON_OUTPUT
      ? [['json', { outputFile: process.env.PLAYWRIGHT_JSON_OUTPUT }] as const]
      : []),
  ],
  use: {
    headless: !process.env.HEADED,
    viewport: { width: 1200, height: 1080 },
    video: {
      mode: 'on',
      size: { width: 1200, height: 1080 },
    },
    trace: 'on',
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
    {
      name: 'chromium-mcp',
      use: {
        browserName: 'chromium',
        trace: 'off',
        video: 'off',
      },
    },
  ],
  outputDir: path.join(reportDir, 'test-results'),
});
