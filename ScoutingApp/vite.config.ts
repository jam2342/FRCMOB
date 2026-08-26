import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Model weights are intentionally excluded from Git, so a CI checkout never has
  // them. The on-device detector degrades on its own (MatchRecorder/VideoFileProcessor
  // surface "Detector unavailable"), so a missing model must NOT fail the whole deploy —
  // that would take the entire app down over one optional PWA feature. Warn instead, and
  // let a release build opt back into hard enforcement via ONDEVICE_MODEL_REQUIRED=true.
  const remoteModelUrl = String(process.env.VITE_ONDEVICE_MODEL_URL || '').trim()
  const bundledModel = resolve(__dirname, 'public/models/frc_robot_detector_v2.onnx')
  const modelMissing = !remoteModelUrl && !existsSync(bundledModel)
  if (mode === 'production' && modelMissing) {
    const message =
      'Missing on-device detector: set VITE_ONDEVICE_MODEL_URL to a public CORS-enabled ONNX URL, or provide public/models/frc_robot_detector_v2.onnx during the build.'
    if (String(process.env.ONDEVICE_MODEL_REQUIRED || '').trim() === 'true') throw new Error(message)
    console.warn(`[vite] ${message} Building without it: on-device detection stays disabled at runtime.`)
  }

  return {
    plugins: [react()],
    envPrefix: ['VITE_', 'NEXT_PUBLIC_'],
    // OpenCV.js is an emscripten bundle that breaks under esbuild dep pre-bundling
    // ("Module is not defined"); exclude it so the dynamic import loads the raw module.
    optimizeDeps: { exclude: ['@techstark/opencv-js'] },
    build: {
      chunkSizeWarningLimit: 700,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes("node_modules")) {
              // OpenCV.js bundles its full WASM as JS (~8 MB). Keep it out of the eager
              // vendor chunk so the dynamic import() in opticalFlow.ts stays lazy — it
              // only loads when on-device stabilization actually runs.
              if (id.includes("@techstark/opencv-js") || id.includes("/mirada/")) return "opencv";
              if (id.includes("react-router")) return "router";
              if (id.includes("react-dom")) return "react-dom";
              if (id.includes("react")) return "react-core";
              return "vendor";
            }

            if (id.includes("/src/pages/ScoutingPage")) return "page-scouting";
            if (id.includes("/src/pages/EventsPage")) return "page-events";
            if (id.includes("/src/pages/TeamCenterPage")) return "page-team-center";
            if (id.includes("/src/pages/MatchCenterPage")) return "page-match-center";
            return undefined;
          },
        },
      },
    },
    test: {
      // Scope vitest to unit tests. Without this it also globs e2e/*.spec.* and
      // tries to run browser guards inside jsdom.
      include: ['src/**/*.{test,spec}.{ts,tsx}', 'e2e/lib/*.test.mjs'],
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      globals: true,
      css: true,
      clearMocks: true,
    },
  }
})
