// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for assistant-memory 02b: the account menu's Memory
 * entry, the Memory dialog (items, proposals, edit, history, empty, admin
 * queue), the memory card (Guided and Expert) and Save as default on the Asset
 * Detail chart with its toast, the settled memory-card lines and Asset Detail
 * opened on a saved default. Component-level harness; every /api route is
 * mocked. Light and dark.
 * node scripts/assistant-memory-shots.mjs [out-dir]   (default: a new temp dir)
 */
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const { chromium } = createRequire(import.meta.url)('playwright')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const entry = `.assistant-memory-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'assistant-memory-shots-'))

const item = (over = {}) => ({
  id: 'mem_1', kind: 'preference', scope: 'user', applies_to: null,
  concept_keys: ['topic:units'], status: 'confirmed', origin: 'chat', version: 2,
  summary: 'Show power in GW', body: { key: 'units.power', value: 'GW' },
  evidence: [{ kind: 'chat', id: 's1', label: 'from this chat' }],
  nomination: null, promoted_from: null, recall_count: 4, apply_count: 3,
  last_used_at: '2026-11-08T10:00:00Z', created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-08T10:00:00Z', ...over,
})
const ITEMS = [
  item(),
  item({ id: 'mem_2', kind: 'chart_spec', origin: 'button', version: 1, applies_to: 'capacity_expansion',
    summary: 'Asset Detail · solar: Active power, Curtailment · duration',
    concept_keys: ['view:asset', 'carrier:solar'], evidence: [], apply_count: 0, last_used_at: null,
    body: { spec: { schema: 'chart_spec@1', view_id: 'asset', chart: { type: 'duration' },
      series: [{ component_class: 'Generator', metric: 'p', unit: 'MW' }] } } }),
  item({ id: 'mem_3', kind: 'lesson', scope: 'org', applies_to: 'capacity_expansion', version: 1,
    summary: 'Check curtailment before adding storage', concept_keys: ['step:economics', 'carrier:solar'],
    body: { text: 'Check curtailment before adding storage' }, apply_count: 0, last_used_at: null,
    evidence: [{ kind: 'project', id: null, label: 'source project (no access)', access: false }] }),
  item({ id: 'mem_4', kind: 'preference', version: 1, summary: 'Answer briefly',
    concept_keys: ['topic:style'], body: { key: 'answer.style', value: 'brief' }, nomination: 'pending',
    apply_count: 0, last_used_at: null }),
]
const PROPOSALS = [item({ id: 'mem_p', status: 'proposed', origin: 'dream', version: 1,
  summary: 'Quote prices in EUR/MWh', body: { key: 'currency', value: 'EUR' }, concept_keys: ['topic:currency'],
  evidence: [{ kind: 'run', id: 'r1', label: 'run of 7 Nov' }], apply_count: 0, last_used_at: null })]
const VERSIONS = { id: 'mem_1', versions: [
  { version: 1, summary: 'Show power in MW', body: {}, evidence: [], edited_by: 'u1', edited_at: '2026-11-01T09:12:00Z' },
  { version: 2, summary: 'Show power in GW', body: {}, evidence: [], edited_by: 'u1', edited_at: '2026-11-08T10:00:00Z' },
] }
const METRICS = [
  { id: 'p', label: 'Active power', unit: 'MW', kind: 'series', origin: 'output', status: 'ok' },
  { id: 'curtailment', label: 'Curtailment', unit: 'MW', kind: 'series', origin: 'derived', status: 'ok' },
  { id: 'capacity_factor', label: 'Capacity factor', unit: 'pu', kind: 'series', origin: 'derived', status: 'ok' },
]
const N = 48
const ASSET = {
  asset: { class: 'Generator', name: 'Solar 1', carrier: 'solar', bus: 'B1', params: {} },
  solve: { source: 'lopf', objective: 1, solve_time: 1, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological',
  categories: ['summary', 'capacity', 'dispatch', 'prices', 'economics'].map(id => ({ id, label: id[0].toUpperCase() + id.slice(1), status: 'ok' })),
  metrics: METRICS, scalars: {}, headline: [],
  index: Array.from({ length: N }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString()),
  periods: null, pct_of_hours: null,
  columns: [{ id: 'p', label: 'Active power', unit: 'MW', metric_id: 'p', agg: null },
    { id: 'curtailment', label: 'Curtailment', unit: 'MW', metric_id: 'curtailment', agg: null }],
  series: { p: Array.from({ length: N }, (_, i) => Math.max(0, 80 * Math.sin((i % 24 - 6) / 12 * Math.PI))),
    curtailment: Array.from({ length: N }, (_, i) => Math.max(0, 15 * Math.sin((i % 24 - 9) / 6 * Math.PI))) },
}

/** The asset response for the metrics and mode the view asked for, as the
 * backend answers: only those columns, and a duration curve sorted high to low. */
function assetFor(metrics, mode) {
  const ids = metrics ? metrics.split(',').filter(Boolean) : ['p']
  const columns = ASSET.columns.filter(c => ids.includes(c.metric_id))
  const series = Object.fromEntries(columns.map(c => {
    const v = ASSET.series[c.id]
    return [c.id, mode === 'duration' ? [...v].sort((a, b) => b - a) : v]
  }))
  const duration = mode === 'duration'
  return { ...ASSET, mode: mode || 'chronological', columns, series,
    index: duration ? ASSET.index.map((_, i) => String(i + 1)) : ASSET.index,
    pct_of_hours: duration ? ASSET.index.map((_, i) => (i + 1) / ASSET.index.length) : null }
}

function api(url, scene) {
  const p = url.pathname.replace(/^\/api/, '')
  const s = url.searchParams
  if (p === '/auth/me') return { id: 'u1', email: 'ana@example.com', status: 'active', is_super_admin: false, org_id: 'o1', role: 'admin' }
  if (p === '/auth/org-members') return [{ id: 'u1', email: 'ana@example.com', role: 'admin' }, { id: 'u2', email: 'ben@example.com', role: 'member' }]
  if (p === '/projects' || p === '/projects/') return [{ id: 'Demo', name: 'Demo', project_kind: null }]
  if (p === '/memory/items') {
    if (scene === 'dialog-empty') return { items: [], can_promote: false }
    if (s.get('nominated') === 'true') return { items: [item({ id: 'mem_n', summary: 'Answer briefly', concept_keys: ['topic:style'], body: { key: 'answer.style', value: 'brief' }, nomination: 'pending', evidence: [{ kind: 'chat', id: 's9', label: 'from a chat' }] })], can_promote: true }
    if (s.get('status') === 'proposed') return { items: PROPOSALS, can_promote: true }
    if (s.get('kind') === 'chart_spec') return { items: scene === 'asset-default' ? [ITEMS[1]] : [], can_promote: true }
    return { items: ITEMS, can_promote: true }
  }
  if (p.startsWith('/memory/items/') && p.endsWith('/versions')) return VERSIONS
  if (p === '/memory/items' || p.startsWith('/memory/')) return item({ id: 'mem_new' })
  if (p.includes('/results/asset/assets') || p.endsWith('/assets')) return { assets: [{ class: 'Generator', name: 'Solar 1', carrier: 'solar', bus: 'B1' }] }
  if (p.includes('/asset')) return assetFor(s.get('metrics'), s.get('mode'))
  return {}
}

let server, browser
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {Toaster} from 'react-hot-toast'
import {MemoryRouter} from 'react-router-dom'
import {AuthProvider} from './auth/AuthProvider'
import {ConfirmationCard} from './components/ConfirmationCard'
import UserMenu from './layout/UserMenu'
import MemoryDialog from './components/memory/MemoryDialog'
import MemorySettledLine from './components/memory/MemorySettledLine'
import AssetDetail from './pages/results/asset/AssetDetail'
import {useUIStore} from './store/uiStore'
import {useChatStore} from './store/chatStore'
import {useMemoryStore} from './store/memoryStore'
import './index.css'
const q = new URLSearchParams(location.search)
const scene = q.get('scene')!
document.documentElement.setAttribute('data-theme', q.get('theme') || 'light')
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, assistantDockOpen: true,
  uiMode: q.get('mode') === 'expert' ? 'expert' : 'guided' } as any)
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
let body: React.ReactNode
if (scene === 'card') {
  useChatStore.setState({ sessionId: 's', pending: { tool_use_id: 'c1', tool_name: 'propose_memory',
    args: { kind: 'preference', summary: 'Show power in GW', body: { key: 'units.power', value: 'GW' },
      concept_keys: ['topic:units', 'view:asset'], project_kind: 'capacity_expansion' },
    safety_tier: 'write', confirmation_token: 't', ttl_seconds: 300, expires_at_epoch_ms: Date.now() + 300_000 } } as any)
  body = <div style={{width: 400, padding: 8}} className="bg-bg"><ConfirmationCard/></div>
} else if (scene === 'menu') {
  // alignItems: the menu's root must not stretch to the box, or its dropdown
  // (top-full) opens at the bottom of the viewport.
  body = <div style={{width: 520, height: 300, padding: 8, display: 'flex', justifyContent: 'flex-end', alignItems: 'flex-start'}} className="bg-bg"><UserMenu/></div>
} else if (scene === 'settled') {
  useMemoryStore.getState().noteDismissed('c2')
  const rows: [string, any][] = [['c1', { state: 'remembered', id: 'mem_1' }], ['c2', { state: 'not_now' }],
    ['c3', { state: 'not_now' }], ['c4', { state: 'expired' }], ['c5', { state: 'no_answer' }]]
  body = <div style={{width: 400, padding: 8}} className="bg-bg">{rows.map(([id, st]) => (
    <div key={id} className="px-3 py-1.5 text-muted text-[11px] leading-snug border-b border-border/40">
      <MemorySettledLine settled={st} toolUseId={id}/></div>))}</div>
} else if (scene.startsWith('asset')) {
  body = <div style={{width: 1200, height: 560}} className="bg-bg text-text"><AssetDetail/></div>
} else {
  const tab = scene === 'dialog-proposals' ? 'proposals' : scene === 'dialog-admin' ? 'suggested' : 'items'
  useMemoryStore.getState().openMemory({ tab: tab as any, editId: scene === 'dialog-edit' ? 'mem_1' : null })
  body = <div style={{width: 1100, height: 760}} className="bg-bg"><MemoryDialog/></div>
}
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={qc}><MemoryRouter><AuthProvider>{body}<Toaster/></AuthProvider></MemoryRouter></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  const port = server.httpServer.address().port
  const shots = [
    ['menu', 'guided'], ['dialog-items', 'guided'], ['dialog-history', 'guided'], ['dialog-edit', 'guided'],
    ['dialog-proposals', 'guided'], ['dialog-admin', 'guided'], ['dialog-empty', 'guided'],
    ['card', 'guided'], ['card', 'expert'], ['settled', 'guided'], ['asset-save', 'expert'], ['asset-save', 'guided'],
    ['asset-default', 'guided'],
  ].filter(([s]) => !process.env.ONLY || s === process.env.ONLY)
  for (const theme of ['light', 'dark']) {
    for (const [scene, mode] of shots) {
      const vw = scene === 'card' || scene === 'settled' ? 420 : scene === 'menu' ? 540 : scene.startsWith('asset') ? 1220 : 1120
      const vh = scene === 'settled' ? 200 : scene === 'card' ? 320 : scene === 'menu' ? 320 : scene.startsWith('asset') ? 640 : 780
      const page = await browser.newPage({ viewport: { width: vw, height: vh }, deviceScaleFactor: 2 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', r => r.abort())
      await page.route(url => url.pathname.startsWith('/api/'), r => r.fulfill({ json: api(new URL(r.request().url()), scene) }))
      await page.goto(`http://127.0.0.1:${port}/${entry}.html?scene=${scene}&mode=${mode}&theme=${theme}`)
      if (scene === 'menu') { await page.locator('button[title="ana@example.com"]').click(); await page.getByText('Memory').waitFor() }
      if (scene === 'card') await page.getByTestId('memory-card').waitFor()
      if (scene.startsWith('dialog')) await page.getByRole('dialog').waitFor()
      if (scene === 'dialog-history') { await page.getByRole('button', { name: 'History' }).first().click(); await page.getByTestId('memory-history').waitFor() }
      if (scene === 'dialog-edit') await page.getByTestId('memory-edit-form').waitFor()
      if (scene === 'settled') await page.getByTestId('memory-settled').first().waitFor()
      if (scene === 'asset-default') await page.getByTestId('asset-default-line').waitFor()
      if (scene === 'asset-save') {
        await page.getByRole('button', { name: 'Chart' }).click()
        await page.getByTestId('asset-save-default').click()
        await page.getByTestId('asset-default-toast').waitFor()
      }
      await page.waitForTimeout(400)
      const file = path.join(out, `memory-${scene}${scene === 'card' || scene === 'asset-save' ? `-${mode}` : ''}-${theme}.png`)
      await page.screenshot({ path: file })
      console.log(file)
      await page.close()
    }
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
