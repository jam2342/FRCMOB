import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Model weights are intentionally excluded from Git. A production build
  // therefore needs a public CORS-enabled URL unless the build context
  // deliberately supplies the local ONNX artifact.
  const remoteModelUrl = String(process.env.VITE_ONDEVICE_MODEL_URL || '').trim()
  const bundledModel = resolve(__dirname, 'public/models/frc_robot_detector_v2.onnx')
  if (mode === 'production' && !remoteModelUrl && !existsSync(bundledModel)) {
    throw new Error(
      'Missing on-device detector: set VITE_ONDEVICE_MODEL_URL to a public CORS-enabled ONNX URL, or provide public/models/frc_robot_detector_v2.onnx during the build.',
    )
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
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      globals: true,
      css: true,
      clearMocks: true,
    },
  }
})
