// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** UIP-01 UX-review shots, Guided and Expert: a Link fix link (before: the read-only card at the top;
 * after: the editable form at the field, focused and highlighted), the year readout, and a refused save
 * (one toast, clear of Save). Real Vite app code (PropertiesPanel, FieldDrawer, ToasterHost), API routes mocked.
 * node scripts/ui-polish-shots.mjs [out-dir]   (default: .scratch/ui-polish/shots)
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
const entry = `.ui-polish-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'ui-polish', 'shots'))
const SCHEMA = JSON.parse(await fs.readFile(path.join(root, 'src/test/fixtures/assetSchema.json'), 'utf8')).Link
const LINK = { name: 'grid_import', bus0: 'grid', bus1: 'hub', carrier: 'AC', p_nom: 100, p_nom_extendable: false, p_nom_min: 0,
  p_nom_max: null, p_min_pu: 0, p_max_pu: 1, efficiency: 0.97, marginal_cost: 0, capital_cost: 0, build_year: 2027, lifetime: 40 }
const BUSES = ['grid', 'hub'].map(name => ({ name, v_nom: 110, carrier: 'AC', x: 0, y: 0, country: '', unit: '', control: 'PQ', sub_network: '' }))
let server, browser
const pageErrors = []
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import PropertiesPanel from './layout/PropertiesPanel'
import {FieldDrawer} from './components/FieldDrawer'
import ToasterHost from './components/ToasterHost'
import {useUIStore} from './store/uiStore'
import './index.css'
const mode = new URLSearchParams(location.search).get('mode') === 'expert' ? 'expert' : 'guided'
useUIStore.setState({ currentProject: 'Demo', uiMode: mode } as any)
;(window as any).ui = useUIStore
import('./layout/flowNavigation').then(m => { (window as any).nav = m })
createRoot(document.getElementById('root')!).render(<MemoryRouter><QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
  <div style={{height:'100vh', background:'var(--color-bg)'}}>
    {mode === 'guided' ? <FieldDrawer>{() => <PropertiesPanel/>}</FieldDrawer>
      : <div style={{position:'fixed',right:0,top:44,bottom:0,width:340,borderLeft:'1px solid var(--color-border)',display:'flex'}}><PropertiesPanel/></div>}
  </div><ToasterHost/></QueryClientProvider></MemoryRouter>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  const target = { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'efficiency' }
  for (const mode of ['guided', 'expert']) {
    const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.route(u => u.protocol.startsWith('http') && u.hostname !== '127.0.0.1', r => r.abort())
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname
      if (r.request().method() === 'PUT') return r.fulfill({ status: 409, json: { detail: 'A frontier study is running: wait for it to finish.' } })
      if (p === '/api/network/links') return r.fulfill({ json: [LINK] })
      if (p === '/api/network/buses') return r.fulfill({ json: BUSES })
      if (p.startsWith('/api/network/catalog/')) return r.fulfill({ json: { component: 'Link', attributes: [] } })
      if (p === '/api/network/asset_schema/Link') return r.fulfill({ json: SCHEMA })
      if (/\/provenance/.test(p)) return r.fulfill({ json: { component: 'Link', name: 'grid_import', fields: {} } })
      return r.fulfill({ json: [] })
    })
    page.on('pageerror', e => { pageErrors.push(e.message) })
    await page.goto('http://127.0.0.1:' + server.httpServer.address().port + '/' + entry + '.html?mode=' + mode)
    await page.waitForFunction(() => window.nav && window.ui)
    const shot = async key => { const f = path.join(out, 'uip01-' + mode + '-' + key + '.png'); await page.screenshot({ path: f }); console.log(f) }
    const select = () => page.evaluate(() => { window.ui.getState().setSelectedComponent({ type: 'Link', name: 'grid_import' }); window.ui.getState().openRightPanel() })
    // before: what the link did (select only): the read-only card at the top, no field in view
    await select()
    if (mode === 'guided') await page.evaluate(t => window.ui.getState().setFieldDrawer(t), target)
    await page.getByText('Build year').first().waitFor()
    await page.waitForTimeout(400)
    await shot('1-before-read-only-and-year')
    // after: the fix link
    await page.evaluate(([m, t]) => (m === 'guided' ? window.nav.openFlowTargetInPlace : window.nav.openFlowTarget)(t), [mode, target])
    await page.locator('[data-field="efficiency"] input').waitFor()
    await page.waitForTimeout(500)
    const focused = await page.evaluate(() => document.activeElement?.closest('[data-field]')?.dataset.field === 'efficiency')
    if (!focused) throw new Error(mode + ': efficiency is not focused')
    await shot('2-after-fix-link-field')
    // a refused save: one toast, not over Save
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await page.getByText(/Save failed/).waitFor()
    await page.waitForTimeout(500)
    const n = await page.getByText(/frontier study is running/).count()
    if (n !== 1) console.log(await page.getByText(/frontier study is running/).allInnerTexts())
    if (n !== 1) throw new Error(mode + ': expected one toast, saw ' + n)
    await shot('3-after-refused-save-one-toast')
    await page.close()
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
