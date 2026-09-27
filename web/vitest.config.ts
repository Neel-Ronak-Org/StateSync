import path from 'node:path'
import { defineConfig } from 'vitest/config'

// Unit tests for pure logic (src/**/*.test.ts): the presentation layer in
// src/viz and anything else with no DOM. The browser suites are Playwright.
export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
})
