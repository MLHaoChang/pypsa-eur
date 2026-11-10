// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for provenance-graph 02 (TCU-07): LedgerReview's out-of-date line on the PV cost
 * row, priced on the option forks, and the same view after re-price, when the GET carries no `stale` for the
 * row and the line is gone. Component-level harness on the render test's fixture payloads, no API calls.
 * LedgerReview is one view for Guided and Expert (UX P5), so one mode is shot.
 * node scripts/ledger-stale-shots.mjs [out-dir]   (default: a new temp dir)
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
const entry = `.ledger-stale-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'ledger-stale-shots-'))
let server, browser
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import {createRoot} from 'react-dom/client'
import LedgerReview from './pages/decision/LedgerReview'
import {ledger, study} from './pages/decision/__fixtures__/payloads'
import {useUIStore} from './store/uiStore'
import './index.css'
const verdict = { field: 'dc_ac_ratio', recorded: 1.2, current_field: 'dc_ac_ratio', current: 1.3, reason: 'scale input changed' }
const hit = (option_id: string, option_label: string) =>
  ({ option_id, option_label, component: 'Generator', name: 'pv', field: 'overnight_cost', stale: verdict })
const state = new URLSearchParams(location.search).get('state')
const payload = state === 'stale'
  ? { ...ledger, stale: { pv_rooftop_eur_per_kw: [hit('bess_pv_2h', 'Battery (2 hours) with PV'), hit('bess_pv_4h', 'Battery (4 hours) with PV')] } }
  : { ...ledger, stale: {} }
const intake = { ...study.intake, pv: { enabled: true, kind: 'rooftop' as const } }
useUIStore.setState({ uiMode: 'guided' } as any)
createRoot(document.getElementById('root')!).render(<div style={{width: 760, padding: 8}}>
  <LedgerReview payload={payload as any} intake={intake} saving={false} error={null} csvUrl="/x.csv" onSave={() => {}} onReset={() => {}} />
</div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const [n, state] of [[1, 'stale'], [2, 'repriced']]) {
    const page = await browser.newPage({ viewport: { width: 780, height: 1400 }, deviceScaleFactor: 2 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
    await page.route(url => url.pathname.startsWith('/api/'), route => route.fulfill({ json: {} }))
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?state=${state}`)
    const row = page.getByTestId('ledger-row-pv_rooftop_eur_per_kw')
    await row.waitFor({ state: 'attached' })
    // An unchanged row sits under the collapsed defaults; open it the way a person would.
    if (!(await row.isVisible())) await page.getByTestId('ledger-defaults-summary').click()
    await row.waitFor()
    const lines = await row.getByTestId('ledger-stale').count()
    if (lines !== (state === 'stale' ? 1 : 0)) throw new Error(`${state}: ${lines} out-of-date lines on the PV row`)
    await row.scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)
    const file = path.join(out, `pg02-${n}-ledger-${state}.png`)
    const box = await row.boundingBox()
    await page.screenshot({ path: file, clip: { x: 0, y: Math.max(0, box.y - 120), width: 780, height: box.height + 240 } })
    console.log(file)
    await page.close()
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
