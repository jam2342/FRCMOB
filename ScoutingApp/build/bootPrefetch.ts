import type { Plugin } from 'vite'
import { loadEnv } from 'vite'
import { bootSuggestedEventsPath } from '../src/bootPath'

// Two things index.html starts before the app's JS has run (on a throttled phone
// the bundle takes ~2 s, and both used to wait for it):
//  1. the startup data request every page makes (suggested events), and
//  2. the opened route's own page chunk, its imports and its CSS, which the router
//     only asked for once the main bundle had executed.
// Web builds with a same-origin API only: the native shells and cross-origin setups
// skip the data request, and the recorder page skips both.

// Hash route -> page module, as in RootApp.tsx (bootPrefetch.test.ts checks that
// every page route there is listed). "/" and unknown routes land on /home.
export const ROUTE_PAGES: Record<string, string> = {
  '/home': 'HomePage',
  '/events': 'EventsPage',
  '/events/export': 'ExportPage',
  '/events/dashboard': 'DataVizPage',
  '/my-team': 'MyTeamPage',
  '/scouting': 'ScoutingPage',
  '/scouting/assignments': 'ScoutingAssignPage',
  '/scouting/pit': 'PitScoutingPage',
  '/scouting/coverage': 'ScoutingCoveragePage',
  '/scouting/practice': 'PracticeScoutingPage',
  '/scouting/calibrate': 'FieldCalibrationPage',
  '/scouting/record': 'OnDeviceRunPage',
  '/match-center': 'MatchCenterPage',
  '/match-center/predictions': 'MatchPredictionPage',
  '/match-center/strategy': 'StrategyBriefingPage',
  '/team-center': 'TeamCenterPage',
  '/compare': 'ComparePage',
  '/compare/alliance-advisor': 'AllianceAdvisorPage',
  '/compare/picklist': 'PicklistPage',
  '/favorites': 'FavoritesPage',
  '/settings': 'SettingsPage',
  '/privacy': 'PrivacyPolicyPage',
  '/terms': 'TermsOfServicePage',
}

type ChunkLike = {
  type: 'chunk'
  fileName: string
  facadeModuleId: string | null
  imports: string[]
  isEntry: boolean
  viteMetadata?: { importedCss?: Set<string> }
}
type BundleLike = Record<string, ChunkLike | { type: 'asset'; fileName: string }>

// For each route: the page chunk, the chunks it imports (minus what the entry
// already loads) and their CSS.
export function routeAssetMap(bundle: BundleLike, base = '/'): Record<string, { js: string[]; css: string[] }> {
  const chunks = Object.values(bundle).filter((item): item is ChunkLike => item.type === 'chunk')
  const byFile = new Map(chunks.map((chunk) => [chunk.fileName, chunk]))
  const entryFiles = new Set<string>()
  const collectEntry = (file: string) => {
    if (entryFiles.has(file)) return
    entryFiles.add(file)
    for (const dep of byFile.get(file)?.imports ?? []) collectEntry(dep)
  }
  for (const chunk of chunks) if (chunk.isEntry) collectEntry(chunk.fileName)

  const out: Record<string, { js: string[]; css: string[] }> = {}
  for (const [route, page] of Object.entries(ROUTE_PAGES)) {
    const pageChunk = chunks.find((chunk) => (chunk.facadeModuleId || '').replace(/\\/g, '/').endsWith(`/src/pages/${page}.tsx`))
    if (!pageChunk) continue
    const js: string[] = []
    const css = new Set<string>()
    const visit = (file: string) => {
      if (entryFiles.has(file) || js.includes(base + file)) return
      const chunk = byFile.get(file)
      if (!chunk) return
      js.push(base + file)
      for (const sheet of chunk.viteMetadata?.importedCss ?? []) css.add(base + sheet)
      for (const dep of chunk.imports) visit(dep)
    }
    visit(pageChunk.fileName)
    out[route] = { js, css: [...css] }
  }
  return out
}

// JSON inside an inline <script>: escape "<" so no value can ever close the tag.
function scriptJson(value: unknown): string {
  return JSON.stringify(value).replace(/</g, '\\u003c')
}

function routePreloadScript(map: Record<string, { js: string[]; css: string[] }>): string {
  return `<script>try{(function(m){var p=(location.hash.replace(/^#/,"").split("?")[0]||"/home").replace(/\\/+$/,"")||"/home";var a=m[p]||m["/home"];if(!a)return;a.js.forEach(function(h){var l=document.createElement("link");l.rel="modulepreload";l.crossOrigin="";l.href=h;document.head.appendChild(l)});a.css.forEach(function(h){var l=document.createElement("link");l.rel="preload";l.as="style";l.href=h;document.head.appendChild(l)})})(${scriptJson(map)})}catch(e){}</script>`
}

export function bootPrefetch(): Plugin {
  let apiBase = ''
  let isBuild = false
  let base = '/'
  return {
    name: 'frcmob-boot-prefetch',
    configResolved(config) {
      const env = loadEnv(config.mode, config.envDir || process.cwd(), ['VITE_', 'NEXT_PUBLIC_'])
      const configured = String(env.VITE_API_URL || env.NEXT_PUBLIC_API_URL || '').trim()
      const resolvedBase = config.mode === 'native' ? '' : configured || (config.command === 'build' ? '/api' : '')
      apiBase = resolvedBase.startsWith('/') ? resolvedBase.replace(/\/+$/, '') : ''
      isBuild = config.command === 'build' && config.mode !== 'native'
      base = config.base || '/'
    },
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!ctx.filename.endsWith('index.html')) return html
        const scripts: string[] = []
        if (apiBase) {
          const url = scriptJson(`${apiBase}${bootSuggestedEventsPath()}`)
          // The empty catch marks the promise handled: offline it rejects before the app
          // takes it, which surfaced as an uncaught "Failed to fetch". The app still gets the rejection.
          scripts.push(`<script>try{window.__frcmobBoot={};var b=fetch(${url},{headers:{Accept:"application/json"}});b.catch(function(){});window.__frcmobBoot[${url}]=b;setTimeout(function(){window.__frcmobBoot={}},30000)}catch(e){}</script>`)
        }
        if (isBuild && ctx.bundle) {
          const map = routeAssetMap(ctx.bundle as unknown as BundleLike, base)
          if (Object.keys(map).length) scripts.push(routePreloadScript(map))
        }
        if (!scripts.length) return html
        // Right after <meta charset>, ahead of the stylesheet: a browser holds an
        // inline script until the stylesheets before it have loaded, which delayed
        // both requests by the whole CSS download (1.3 s on a throttled phone).
        const block = scripts.join('\n    ')
        const charset = html.match(/<meta charset=[^>]*>/i)
        if (charset?.index !== undefined) {
          const at = charset.index + charset[0].length
          return `${html.slice(0, at)}\n    ${block}${html.slice(at)}`
        }
        return html.replace(/<head>/i, `<head>\n    ${block}`)
      },
    },
  }
}
