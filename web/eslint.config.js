import js from '@eslint/js'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

// SECURITY RULES (the reason this config is more than the template):
// the app renders database rows, document text and model output, all
// untrusted. None of it may become HTML or code. These rules make the unsafe
// constructs a lint failure instead of a code-review hope.
const noHtmlSinks = [
  'error',
  {
    selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]",
    message: 'Do not assign innerHTML/outerHTML: render text with React instead.',
  },
  {
    selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
    message: 'insertAdjacentHTML is an HTML sink; render text with React instead.',
  },
  {
    selector: "CallExpression[callee.property.name='write'][callee.object.name='document']",
    message: 'document.write is an HTML sink.',
  },
]

export default defineConfig([
  globalIgnores(['dist', 'playwright-report', 'test-results']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    plugins: { react },
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    rules: {
      'react/no-danger': 'error',
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      'no-restricted-syntax': noHtmlSinks,
    },
  },
  {
    // shadcn/ui components are vendored source; they export variants next to
    // components by design.
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
])
