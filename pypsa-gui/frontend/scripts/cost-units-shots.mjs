// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for ui-modern 05d: the Generator edit drawer's cost fields on a tonne bus
 * (EUR/t forms and the per-tonne note) and on an energy bus (EUR/kW forms, no note), Guided and Expert.
 * Component-level harness (GeneratorCard in detail mode, Edit opened); every /api route is mocked.
 * node scripts/cost-units-shots.mjs [out-dir]   (default: .scratch/ui-modern/shots/05d)
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const { chromium } = createRequire(import.meta.url)('playwright')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const entry = `.cost-units-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'ui-modern', 'shots', '05d'))
const schemas = JSON.parse(await fs.readFile(path.join(root, 'src', 'test', 'fixtures', 'assetSchema.json'), 'utf8'))

const TONNE = 'Ammonia plant'
const tonneUnits = { component: 'Generator', name: TONNE, units: { p_nom: 't/h', p_nom_min: 't/h', p_nom_max: 't/h' }, costs: {
  overnight_cost: { unit: 'EUR/(t/h)', scale: 1 }, fom_cost: { unit: 'EUR/(t/h)/yr', scale: 1 },
  capital_cost: { unit: 'EUR/(t/h)/yr', scale: 1 }, marginal_cost: { unit: 'EUR/t', scale: 1 } } }
const GENS = {
  tonne: { name: TONNE, bus: 'NH3', carrier: 'ammonia', p_nom: 5, p_nom_extendable: true, p_nom_min: 0, p_nom_max: null,
    p_min_pu: 0, p_max_pu: 1, marginal_cost: 40, overnight_cost: 900000, fom_cost: 18000, capital_cost: 80000,
    efficiency: 1, committable: false, control: 'PQ', build_year: 2030, lifetime: 25 },
  energy: { name: 'Gas unit', bus: 'B1', carrier: 'gas', p_nom: 100, p_nom_extendable: true, p_nom_min: 0, p_nom_max: null,
    p_min_pu: 0, p_max_pu: 1, marginal_cost: 52, overnight_cost: 650000, fom_cost: 12000, capital_cost: 60000,
    efficiency: 0.55, committable: false, control: 'PQ', build_year: 2030, lifetime: 30 },
}

let server, browser
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {MemoryRouter} from 'react-router-dom'
import {GeneratorCard} from './layout/PropertiesPanel'
import {useUIStore} from './store/uiStore'
import './index.css'
const q = new URLSearchParams(location.search)
const GENS: any = ${JSON.stringify(GENS)}
document.documentElement.setAttribute('data-theme', 'light')
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, uiMode: q.get('mode') === 'expert' ? 'expert' : 'guided' } as any)
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>
    <div style={{ width: 380, padding: 12 }} className="bg-bg text-text text-[11px]"><GeneratorCard gen={GENS[q.get('asset')!]} mode="detail" /></div>
  </MemoryRouter></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch()
  const port = server.httpServer.address().port
  for (const asset of ['tonne', 'energy']) for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ viewport: { width: 420, height: 1400 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    const page = await ctx.newPage()
    await page.route(u => u.protocol.startsWith('http') && u.hostname !== '127.0.0.1', r => r.abort())
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname
      const units = p.match(/^\/api\/network\/asset_schema\/Generator\/units\/(.+)$/)
      if (units) return r.fulfill({ json: decodeURIComponent(units[1]) === TONNE ? tonneUnits
        : { component: 'Generator', name: decodeURIComponent(units[1]), units: {}, costs: {} } })
      if (p.includes('/provenance/')) return r.fulfill({ json: { component: 'Generator', name: 'x', fields: {} } })
      const sch = p.match(/^\/api\/network\/asset_schema\/([^/]+)$/)
      if (sch) return r.fulfill(schemas[sch[1]] ? { json: schemas[sch[1]] } : { status: 404, json: { detail: 'none' } })
      if (p === '/api/memory/suggestions') return r.fulfill({ json: { fields: {}, saveable: [] } })
      const cat = p.match(/^\/api\/network\/catalog\/(.+)$/)
      if (cat) return r.fulfill({ json: { component: decodeURIComponent(cat[1]), attributes: [] } })
      if (p.endsWith('/generator_profiles')) return r.fulfill({ json: {} })
      if (r.request().method() === 'GET' && /(buses|carriers|timeseries)$/.test(p)) return r.fulfill({ json: [] })
      return r.fulfill({ json: {} })
    })
    page.on('console', m => m.type()==='error' && console.error(m.text().slice(0,300))); page.on('pageerror', e => console.error('pageerror', e.stack.split('\n').slice(0,4).join(' | ')))
    await page.goto(`http://127.0.0.1:${port}/${entry}.html?asset=${asset}&mode=${mode}`)
    await page.getByRole('button', { name: /Edit Generator/ }).click()
    await page.getByText('Overnight cost').first().waitFor()
    await page.waitForTimeout(600)
    const file = path.join(out, `${asset}-${mode}.png`)
    await page.screenshot({ path: file, fullPage: true })
    console.log(file, 'note:', await page.getByTestId('asset-tonne-cost-note').count())
    await ctx.close()
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
