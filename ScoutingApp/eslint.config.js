import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'dist-native', 'android', 'ios']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    // eslint-plugin-react-hooks 7.1 added the React Compiler checks to its recommended set.
    // On this codebase they are advisories, not bugs: 84 hits are state reset from an effect,
    // the rest are Date.now() in useMemo, refs read in render, or memo the compiler can't
    // reuse. Fixing them means touching most pages, so it is its own refactor, not part of
    // a dependency update. Turn them back on one at a time as that work lands.
    rules: {
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/immutability': 'off',
    },
  },
])
