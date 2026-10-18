// Serves a production build for e2e/regression against production's public data,
// read-only: every write to /api is answered 403 here, so a check can't touch real
// data (signed-in flows use the mocks in e2e/regression/mocks instead).
// cors: false matters: Vite's preview otherwise sends `Vary: Origin`, which production
// (Vercel) doesn't, and that makes service-worker cache lookups miss, so offline
// checks would fail here while the real site works.
import { defineConfig, mergeConfig } from 'vite'
import base from './vite.config'

const target = process.env.REGRESSION_API_TARGET || 'https://141-148-171-128.sslip.io'

const proxy = {
  '/api': {
    target,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ''),
    bypass: (req: { method?: string }, res: { statusCode: number; end: (body: string) => void }) => {
      if (req.method && !['GET', 'HEAD', 'OPTIONS'].includes(req.method)) {
        res.statusCode = 403
        res.end('read-only proxy')
        return false
      }
      return undefined
    },
  },
}

export default defineConfig((env) => {
  const resolved = typeof base === 'function' ? base(env) : base
  return mergeConfig(resolved, { preview: { proxy, cors: false } })
})
