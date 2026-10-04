import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { validateOnDeviceModel } from './build/modelAssets'
import { isolateRecorderPage } from './build/isolateRecorder'

export default defineConfig(({ mode }) => ({
  plugins: [react(), validateOnDeviceModel(), isolateRecorderPage(), {
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
    // record.html is the same app served cross-origin isolated (see build/isolateRecorder.ts).
    rollupOptions: { input: { main: 'index.html', record: 'record.html' } },
  },
  test: {
    // Scope vitest to unit tests. Without this it also globs e2e/*.spec.* and
    // tries to run browser guards inside jsdom.
    include: ['src/**/*.{test,spec}.{ts,tsx}', 'e2e/lib/*.test.mjs', 'build/*.test.ts'],
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
    css: true,
    clearMocks: true,
  },
}))
