// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for assistant-memory 03b: Save for reuse on a read-out row
 * (on focus) and its toasts, the "Used before" chips under an Expert edit form (one
 * marked current, one cost chip), the pick card, a refusal in its place, a 409 with
 * "Preview again", the Memory dialog row, and the chips and card at a narrow width.
 * Component-level harness; every /api route is mocked. Light and dark.
 * node scripts/parameter-memory-shots.mjs [out-dir]   (default: a new temp dir)
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
const entry = `.parameter-memory-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'parameter-memory-shots-'))
const schemas = JSON.parse(await fs.readFile(path.join(root, 'src', 'test', 'fixtures', 'assetSchema.json'), 'utf8'))

const chip = (over = {}) => ({
  id: 'mem_1', version: 2, scope: 'user', value: 250, unit: 'MW', currency: null, currency_year: null,
  carrier: 'gas', current: false, saved_at: '2026-11-03T10:00:00+00:00',
  source: { label: 'North Sea hub 2030', id: 'p1', access: true }, ...over,
})
const suggestions = {
  fields: {
    p_nom: [chip(), chip({ id: 'mem_2', value: 100, current: true, saved_at: '2026-10-21T09:00:00+00:00',
      source: { label: 'Baltic retrofit', id: 'p2', access: true } })],
    overnight_cost: [chip({ id: 'mem_3', value: 780_000, unit: 'EUR/MW', currency: 'EUR', currency_year: 2024 })],
    efficiency: [chip({ id: 'mem_4', value: 0.58, unit: 'fraction', source: { label: null, id: null, access: false } })],
  },
  saveable: ['p_nom', 'marginal_cost', 'efficiency', 'overnight_cost'],
}
const preview = {
  version: 1, kind: 'parameter_edit', tool: 'update_component', complete: true,
  says: 'gas p_nom 100 → 250 MW', digest: 'f'.repeat(64), card_digest: 'c'.repeat(64),
  rows: [{ component_class: 'Generator', name: 'gas', field: 'p_nom', label: 'Capacity', old: 100, new: 250,
    unit: 'MW', role: 'typed' }],
  memory_note: '',
  memory: { item: 'mem_1', version: 2, field: 'p_nom', label: 'Capacity', value: 250, unit: 'MW', unit_display: 'MW',
    currency: null, currency_year: null, carrier: 'gas',
    target: { component_class: 'Generator', name: 'gas', value: 100, source: 'pack:eu-dea' } },
}
const paramItem = {
  id: 'mem_1', kind: 'parameter', scope: 'user', applies_to: 'capacity_expansion',
  concept_keys: ['carrier:gas', 'param:Generator.p_nom'], status: 'confirmed', origin: 'button', version: 2,
  summary: 'Generator.p_nom = 250 MW',
  body: { component_class: 'Generator', field: 'p_nom', value: 250, unit: 'MW', carrier: 'gas' },
  evidence: [{ kind: 'project', id: 'p1', label: 'North Sea hub 2030', access: true, at: '2026-11-03T10:00:00+00:00' }],
  nomination: null, promoted_from: null, recall_count: 0, apply_count: 3, last_used_at: '2026-11-10T08:00:00+00:00',
  created_at: '2026-10-01T10:00:00+00:00', updated_at: '2026-11-03T10:00:00+00:00',
}
const costItem = {
  ...paramItem, id: 'mem_3', version: 1, summary: 'Generator.marginal_cost = 52 EUR/MWh',
  concept_keys: ['carrier:gas', 'param:Generator.marginal_cost'], apply_count: 0, last_used_at: null,
  body: { component_class: 'Generator', field: 'marginal_cost', value: 52, unit: 'EUR/MWh', currency: 'EUR',
    currency_year: 2024, carrier: 'gas' },
}

function api(url, method, scene) {
  const p = url.pathname
  if (p.startsWith('/api/network/asset_schema/')) {
    const cls = p.split('/')[4]
    return p.includes('/provenance/') ? [200, { component: cls, name: 'gas', fields: {} }] : [200, schemas[cls]]
  }
  if (p === '/api/memory/suggestions') return [200, suggestions]
  if (p.endsWith('/preview')) {
    if (scene === 'refusal') return [422, { detail: { error_kind: 'currency_mismatch',
      message: 'Capacity: this value was saved in USD and this project uses EUR. Nothing is converted.' } }]
    return [200, preview]
  }
  if (p === '/api/network/memory_parameter/apply') return [409, { detail: { error_kind: 'memory_stale',
    message: 'This saved value changed since it was shown. Pick it again.' } }]
  if (p === '/api/memory/parameters') {
    return [201, { ...paramItem, version: scene === 'toast-resave' ? 3 : 1, created: scene !== 'toast-resave' }]
  }
  if (p === '/api/memory/items') return [200, { items: [paramItem, costItem], can_promote: false }]
  if (p === '/api/auth/me') return [200, { id: 'u1', email: 'ana@example.com', name: 'Ana', role: 'member' }]
  return [200, {}]
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
import {AssetFieldsEdit, assetReadoutItems} from './layout/properties/AssetFields'
import {ExpandedProps, Section} from './layout/properties/cardKit'
import {useSaveAction} from './components/memory/ParameterMemory'
import MemoryDialog from './components/memory/MemoryDialog'
import {useUIStore} from './store/uiStore'
import {useMemoryStore} from './store/memoryStore'
import {useAssetSchema} from './hooks/useAssetSchema'
import './index.css'
const q = new URLSearchParams(location.search)
const scene = q.get('scene')!
const width = Number(q.get('w') || 380)
document.documentElement.setAttribute('data-theme', q.get('theme') || 'light')
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, uiMode: 'expert' } as any)
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const GEN = { name: 'gas', bus: 'B1', carrier: 'gas', p_nom: 100, marginal_cost: 52, efficiency: 0.55,
  overnight_cost: 650000, lifetime: 30 }
// The form holds display values (efficiency in %, cost in EUR/kW), as the cards fill it.
const fs = { ...Object.fromEntries(Object.entries(GEN).map(([k, v]) => [k, String(v)])), efficiency: '55', overnight_cost: '650' }
function ReadOut() {
  const schema = useAssetSchema('Generator').data
  const action = useSaveAction('Generator', 'gas', GEN)
  if (!schema) return null
  return <Section title="Generator Properties"><ExpandedProps items={assetReadoutItems(schema, GEN)} action={action}/></Section>
}
function Edit() {
  const schema = useAssetSchema('Generator').data
  const [form, setForm] = React.useState(fs)
  if (!schema) return null
  return <div className="grid grid-cols-2 gap-x-2 gap-y-1"><AssetFieldsEdit schema={schema} fs={form} set={setForm}
    current={GEN} carriers={[]} buses={[]} suggestions/></div>
}
let body: React.ReactNode
if (scene === 'dialog') {
  useMemoryStore.getState().openMemory({ tab: 'items' as any, editId: null })
  body = <div style={{width: 1100, height: 520}} className="bg-bg"><MemoryDialog/></div>
} else if (scene.startsWith('readout') || scene.startsWith('toast')) {
  body = <div style={{width, padding: 12}} className="bg-bg text-text"><ReadOut/></div>
} else {
  body = <div style={{width, padding: 12}} className="bg-bg text-text text-[11px]"><Edit/></div>
}
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={qc}><MemoryRouter><AuthProvider>{body}<Toaster/></AuthProvider></MemoryRouter></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  const port = server.httpServer.address().port
  // [scene, width, viewport height]
  const shots = [
    ['readout-focus', 380, 520], ['toast-created', 380, 520], ['toast-resave', 380, 520],
    ['chips', 380, 900], ['card', 380, 900], ['refusal', 380, 900], ['stale', 380, 900],
    ['card-narrow', 280, 900], ['dialog', 1120, 540],
  ].filter(([s]) => !process.env.ONLY || s === process.env.ONLY)
  for (const theme of ['light', 'dark']) {
    for (const [scene, w, vh] of shots) {
      const page = await browser.newPage({ viewport: { width: w + 40, height: vh }, deviceScaleFactor: 2 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', r => r.abort())
      await page.route(url => url.pathname.startsWith('/api/'), r => {
        const [status, json] = api(new URL(r.request().url()), r.request().method(), scene)
        return r.fulfill({ status, json })
      })
      await page.goto(`http://127.0.0.1:${port}/${entry}.html?scene=${scene}&theme=${theme}&w=${w}`)
      if (scene === 'dialog') await page.getByRole('dialog').waitFor()
      if (scene.startsWith('readout') || scene.startsWith('toast')) {
        const save = page.getByTestId('memory-save-p_nom')
        await save.waitFor({ state: 'attached' })
        await save.focus()
        if (scene.startsWith('toast')) { await save.click(); await page.getByRole('status').filter({ hasText: /Saved/ }).first().waitFor() }
      }
      if (['chips', 'card', 'refusal', 'stale', 'card-narrow'].includes(scene)) {
        await page.getByTestId('memory-chips-p_nom').waitFor()
        if (scene !== 'chips') {
          await page.getByTestId('memory-chip').first().click()
          await page.getByTestId(scene === 'refusal' ? 'memory-pick-refusal' : 'memory-pick-apply').waitFor()
          if (scene === 'stale') { await page.getByTestId('memory-pick-apply').click(); await page.getByTestId('memory-pick-again').waitFor() }
        }
        await page.getByTestId('memory-chips-p_nom').scrollIntoViewIfNeeded()
      }
      await page.waitForTimeout(400)
      const file = path.join(out, `param-${scene}-${theme}.png`)
      const clip = ['chips', 'card', 'refusal', 'stale', 'card-narrow'].includes(scene)
      if (clip) await page.locator('[data-testid="memory-chips-p_nom"]').evaluate(el => el.closest('[data-testid^="asset-group-"]').scrollIntoView())
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
