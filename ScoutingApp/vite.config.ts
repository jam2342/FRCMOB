import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { validateOnDeviceModel } from './build/modelAssets'
import { isolateRecorderPage } from './build/isolateRecorder'
import { bootPrefetch } from './build/bootPrefetch'
import { contentSecurityPolicy } from './build/contentSecurityPolicy'

// VITE_PREACT=1 builds the app on Preact instead of React: the same components, through
// preact/compat. It cuts the startup JavaScript by about 60 KB gzipped (163 -> 103 KB),
// most of it React DOM. Tests always run on React (Testing Library needs it).
const preact = process.env.VITE_PREACT === '1'
const preactAliases = [
  { find: /^react-dom\/client$/, replacement: 'preact/compat/client' },
  { find: /^react-dom$/, replacement: 'preact/compat' },
  { find: /^react\/jsx-runtime$/, replacement: 'preact/jsx-runtime' },
  { find: /^react\/jsx-dev-runtime$/, replacement: 'preact/jsx-dev-runtime' },
  { find: /^react$/, replacement: 'preact/compat' },
]

export default defineConfig(({ mode }) => ({
  resolve: { alias: preact && mode !== 'test' ? preactAliases : [] },
  plugins: [react(), validateOnDeviceModel(), isolateRecorderPage(), bootPrefetch(), contentSecurityPolicy(), {
    name: 'native-safe-area-viewport',
    transformIndexHtml: (html) => mode === 'native'
      ? html.replace('width=device-width, initial-scale=1.0', 'width=device-width, initial-scale=1.0, viewport-fit=cover')
      : html,
  }],
  envPrefix: ['VITE_', 'NEXT_PUBLIC_'],
  // OpenCV.js is an emscripten bundle that breaks under esbuild dep pre-bundling
  // ("Module is not defined"); exclude it so the dynamic import loads the raw module.
  optimizeDeps: { exclude: ['@techstark/opencv-js'] },
  build: {
    outDir: mode === 'native' ? 'dist-native' : 'dist',
    chunkSizeWarningLimit: 700,
    // The licenses of every package in the bundle, linked from the site footer.
    license: { fileName: 'third-party-licenses.txt' },
    // record.html is the same app served cross-origin isolated (see build/isolateRecorder.ts).
    rollupOptions: { input: { main: 'index.html', record: 'record.html' } },
  },
  test: {
    // Scope vitest to unit tests. Without this it also globs e2e/*.spec.* and
    // tries to run browser guards inside jsdom.
    include: ['src/**/*.{test,spec}.{ts,tsx}', 'e2e/lib/*.test.mjs', 'build/*.test.ts'],
    environment: 'jsdom',
    // One jsdom per worker thread, a fresh VM context per test file. The default pool
    // started a process and loaded jsdom (~2 s) for every one of the 200+ files: about
    // two thirds of the run was setup. The tests stay isolated per file either way.
    pool: 'vmThreads',
    setupFiles: './src/test/setup.ts',
    globals: true,
    css: true,
    clearMocks: true,
  },
}))
